"""
Market Scanner
==============

Orchestrates the full end-to-end scan pipeline:

1. Load ticker universe (S&P 500 + NASDAQ 100 + optional custom list)
2. Fetch OHLCV history + latest snapshot for each ticker
3. Run dip detection engine
4. For tickers with dip_score > threshold: run sentiment engine
5. Compute market context (SPY, VIX)
6. Run decision engine
7. Persist signals to DB
8. Broadcast updates via WebSocket

Designed to run:
- Full scan: every SCAN_INTERVAL_SECONDS (default 5 min) via Celery beat
- Watchlist scan: every WATCHLIST_SCAN_SECONDS (default 1 min) for saved tickers
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.dip_engine import compute_dip_score, DipScoreResult
from core.sentiment_engine import score_ticker_news, SentimentResult
from core.decision_engine import make_decision, MarketContext, DecisionResult
from services.polygon_client import PolygonClient
from services.news_service import NewsService
from db.session import AsyncSessionFactory
from models.orm import Signal, Ticker
from sqlalchemy import select, delete

logger = structlog.get_logger(__name__)

# Canonical universe lists (tickers only — fetch from DB after seeding)
SP500_UNIVERSE  = "SP500"
NDX100_UNIVERSE = "NASDAQ100"


class MarketScanner:
    """
    Full market scan orchestrator.

    Usage (standalone):
        scanner = MarketScanner()
        signals = await scanner.run_full_scan()
    """

    def __init__(
        self,
        dip_threshold: float = settings.DIP_SCORE_THRESHOLD,
        max_tickers: int = settings.MAX_TICKERS_PER_SCAN,
    ):
        self._dip_threshold    = dip_threshold
        self._max_tickers      = max_tickers

    async def run_full_scan(
        self,
        symbols: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Run a complete scan.  If symbols is None, uses DB universe.
        Returns list of signal dicts.
        """
        async with AsyncSessionFactory() as db:
            if symbols is None:
                symbols = await self._load_universe(db)

            symbols = symbols[: self._max_tickers]
            logger.info("scan_started", ticker_count=len(symbols))

            # Step 1: Market context (SPY, VIX)
            market_ctx = await self._fetch_market_context()

            # Step 2: Batch price snapshots (efficient — 1 API call per 250 tickers)
            async with PolygonClient() as poly:
                snapshots = await poly.get_snapshots_bulk(symbols)

            # Step 3: Process each ticker concurrently in bounded batches
            semaphore = asyncio.Semaphore(20)  # max 20 concurrent bar fetches
            tasks = [
                self._process_ticker(symbol, snapshots.get(symbol, {}), market_ctx, semaphore)
                for symbol in symbols
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            signals: list[dict] = []
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("ticker_scan_failed", error=str(r))
                    continue
                if r is not None:
                    signals.append(r)

            # Step 4: Persist to DB
            await self._persist_signals(signals, db)
            logger.info("scan_complete", signals_generated=len(signals))
            return signals

    async def _process_ticker(
        self,
        symbol: str,
        snapshot: dict,
        market_ctx: MarketContext,
        semaphore: asyncio.Semaphore,
    ) -> Optional[dict]:
        """Full pipeline for a single ticker.  Returns signal dict or None."""
        async with semaphore:
            try:
                return await self._process_ticker_inner(symbol, snapshot, market_ctx)
            except Exception as e:
                logger.debug("ticker_processing_error", symbol=symbol, error=str(e))
                return None

    async def _process_ticker_inner(
        self,
        symbol: str,
        snapshot: dict,
        market_ctx: MarketContext,
    ) -> Optional[dict]:
        # ── Fetch 1+ year of daily bars ────────────────────────────────────────
        async with PolygonClient() as poly:
            bars = await poly.get_daily_bars(
                symbol=symbol,
                from_date=date.today() - timedelta(days=400),
                to_date=date.today(),
            )
            # Ticker details for earnings date
            details = await poly.get_ticker_details(symbol)

        if bars.empty or len(bars) < 252:
            return None

        price = float(bars["close"].iloc[-1])
        if price < settings.MIN_PRICE:
            return None

        # Earnings date
        next_earnings = None
        earnings_str = details.get("next_earnings_report_date")
        if earnings_str:
            try:
                next_earnings = pd.Timestamp(earnings_str)
            except Exception:
                pass

        # ── Dip score ──────────────────────────────────────────────────────────
        dip: Optional[DipScoreResult] = compute_dip_score(
            bars,
            min_avg_volume=settings.MIN_AVG_VOLUME,
            next_earnings_dt=next_earnings,
        )
        if dip is None or dip.composite < self._dip_threshold:
            return None

        # ── Sentiment (only for qualifying tickers) ────────────────────────────
        today_return = float(
            (bars["close"].iloc[-1] - bars["close"].iloc[-2]) / bars["close"].iloc[-2]
        )
        sent: Optional[SentimentResult] = None
        async with NewsService() as ns:
            articles = await ns.get_news_for_ticker(symbol)
        if articles:
            sent = score_ticker_news(articles, price_change_pct=today_return)

        # ── Decision ───────────────────────────────────────────────────────────
        decision: DecisionResult = make_decision(dip, sent, market_ctx)

        # ── Build output dict ──────────────────────────────────────────────────
        signal = {
            "symbol": symbol,
            **dip.to_dict(),
            **(sent.to_dict() if sent else {"sentiment_score": 50.0, "sentiment_label": "neutral"}),
            **decision.to_dict(),
            "spy_trend": market_ctx.spy_trend,
            "sector_rs": market_ctx.sector_rs,
            "vix_level": market_ctx.vix_level,
            "price": price,
        }
        return signal

    async def _fetch_market_context(self) -> MarketContext:
        """Compute SPY trend, VIX level."""
        try:
            async with PolygonClient() as poly:
                spy_bars = await poly.get_daily_bars(
                    "SPY",
                    from_date=date.today() - timedelta(days=400),
                    to_date=date.today(),
                )
                vix_snapshot = await poly.get_snapshot("VXX")  # VXX as VIX proxy

            if spy_bars.empty:
                return self._default_market_context()

            spy_close = spy_bars["close"]
            spy_price = float(spy_close.iloc[-1])
            spy_ma20  = float(spy_close.rolling(20).mean().iloc[-1])
            spy_ma200 = float(spy_close.rolling(200).mean().iloc[-1])
            spy_rsi   = float(
                __import__("ta").momentum.RSIIndicator(spy_close, 14).rsi().iloc[-1]
            )

            spy_vs_ma200 = (spy_price - spy_ma200) / spy_ma200

            if spy_price > spy_ma20 and spy_price > spy_ma200:
                spy_trend = "bullish"
            elif spy_price < spy_ma20 and spy_price < spy_ma200:
                spy_trend = "bearish"
            else:
                spy_trend = "neutral"

            day_item = vix_snapshot.get("day", {})
            vix_level = float(day_item.get("c", 18.0))

            return MarketContext(
                spy_trend=spy_trend,
                spy_rsi=spy_rsi,
                spy_vs_ma200_pct=spy_vs_ma200,
                vix_level=vix_level,
                sector_rs=1.0,  # updated per-ticker in decision engine
            )
        except Exception as e:
            logger.warning("market_context_fetch_failed", error=str(e))
            return self._default_market_context()

    def _default_market_context(self) -> MarketContext:
        return MarketContext(
            spy_trend="neutral",
            spy_rsi=50.0,
            spy_vs_ma200_pct=0.0,
            vix_level=20.0,
            sector_rs=1.0,
        )

    async def _load_universe(self, db: AsyncSession) -> list[str]:
        """Load active tickers from DB that pass liquidity filters."""
        result = await db.execute(
            select(Ticker.symbol)
            .where(
                Ticker.is_active == True,
                Ticker.market_cap >= settings.MIN_MARKET_CAP,
                Ticker.avg_volume >= settings.MIN_AVG_VOLUME,
            )
            .limit(self._max_tickers)
        )
        symbols = [row[0] for row in result.fetchall()]
        if not symbols:
            # Fallback to a minimal hardcoded universe for bootstrapping
            symbols = _DEFAULT_UNIVERSE
        return symbols

    async def _persist_signals(self, signals: list[dict], db: AsyncSession) -> None:
        """Upsert signals — delete today's existing signals and insert fresh."""
        from datetime import datetime
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # Delete today's signals for these symbols to avoid duplicates
        syms = [s["symbol"] for s in signals]
        if syms:
            await db.execute(
                delete(Signal).where(
                    Signal.symbol.in_(syms),
                    Signal.ts >= today_start,
                )
            )

        for s in signals:
            obj = Signal(
                symbol=s["symbol"],
                dip_score=s.get("dip_score"),
                rsi_score=s.get("rsi_score"),
                bb_score=s.get("bb_score"),
                ma_deviation_score=s.get("ma_deviation_score"),
                volume_score=s.get("volume_score"),
                zscore_score=s.get("zscore_score"),
                mean_rev_score=s.get("mean_rev_score"),
                support_score=s.get("support_score"),
                sentiment_score=s.get("sentiment_score"),
                sentiment_label=s.get("sentiment_label"),
                news_type=s.get("news_type"),
                composite_score=s.get("composite_score"),
                bias=s.get("bias"),
                confidence=s.get("confidence"),
                reasoning=s.get("reasoning"),
                spy_trend=s.get("spy_trend"),
                sector_rs=s.get("sector_rs"),
                vix_level=s.get("vix_level"),
                entry_low=s.get("entry_low"),
                entry_high=s.get("entry_high"),
                stop_loss=s.get("stop_loss"),
                target_1=s.get("target_1"),
                target_2=s.get("target_2"),
                risk_reward=s.get("risk_reward"),
                price=s.get("price"),
                rsi_14=s.get("rsi_14"),
                bb_pct=s.get("bb_pct"),
                ma20=s.get("ma20"),
                ma50=s.get("ma50"),
                ma200=s.get("ma200"),
                atr=s.get("atr"),
                zscore=s.get("zscore"),
                news_headlines=s.get("headlines", []),
            )
            db.add(obj)

        await db.commit()


# ── Fallback minimal universe ──────────────────────────────────────────────────
_DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "UNH", "LLY", "JPM", "V", "XOM", "JNJ", "PG", "MA", "HD", "CVX",
    "MRK", "ABBV", "PEP", "COST", "KO", "BAC", "WMT", "AVGO", "MCD",
    "PFE", "CSCO", "TMO", "ABT", "ORCL", "CRM", "NFLX", "ACN", "TXN",
    "AMD", "DHR", "NEE", "LIN", "PM", "BMY", "AMGN", "UPS", "QCOM",
    "LOW", "MS", "GS", "C", "WFC", "INTC", "T", "VZ", "BA", "CAT",
    "DE", "MMM", "GE", "RTX", "HON", "IBM", "SBUX", "INTU", "ISRG",
    "NOW", "SPGI", "BLK", "GILD", "ADI", "MDLZ", "REGN", "ZTS", "MO",
    "LRCX", "CI", "SYK", "EW", "PLD", "AMT", "SPG", "WM", "DUK",
    "SO", "AEP", "EXC", "D", "CL", "FISV", "ICE", "MCO", "CB",
]
