"""
FastAPI Application
===================

REST + WebSocket API for the quant platform dashboard.

Endpoints:
  GET  /api/signals          — paginated signal list with filters
  GET  /api/signals/{id}     — single signal detail
  GET  /api/tickers/{symbol} — ticker info + latest signal
  POST /api/watchlist        — add to watchlist
  GET  /api/watchlist        — list watchlist
  DELETE /api/watchlist/{id} — remove from watchlist
  POST /api/backtest         — run a backtest
  GET  /api/backtest/{id}    — fetch backtest result
  GET  /api/market/context   — current SPY/VIX state
  WS   /ws                   — real-time signal stream
"""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog
from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from db.session import get_db, engine
from models.orm import Base, Signal, Ticker, Watchlist, BacktestResult
from models.schemas import (
    SignalResponse, SignalListResponse,
    WatchlistAdd, WatchlistResponse,
    BacktestRequest, BacktestResponse,
    TickerResponse,
)
from services.ws_manager import ws_manager
from services.scanner import MarketScanner

logger = structlog.get_logger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (migrations handle this in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database_tables_created")
    yield
    await engine.dispose()


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Quant Dip Finder API",
    version="1.0.0",
    description="Real-time stock dip detection and trading signal platform",
    lifespan=lifespan,
)

import os as _os

_FRONTEND_URL = _os.environ.get("FRONTEND_URL", "")
_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    *([_FRONTEND_URL] if _FRONTEND_URL else []),
    # Railway preview URLs
    "https://*.up.railway.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_origin_regex=r"https://.*\.up\.railway\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Signals ────────────────────────────────────────────────────────────────────

@app.get("/api/signals", response_model=SignalListResponse)
async def list_signals(
    bias: Optional[str]         = Query(None, description="BUY | WATCH | AVOID"),
    min_dip_score: float        = Query(0.0,  ge=0, le=100),
    min_confidence: float       = Query(0.0,  ge=0, le=100),
    sector: Optional[str]       = Query(None),
    sentiment: Optional[str]    = Query(None, description="positive | neutral | negative"),
    sort_by: str                = Query("composite_score", description="Field to sort by"),
    page: int                   = Query(1, ge=1),
    page_size: int              = Query(25, ge=1, le=100),
    db: AsyncSession            = Depends(get_db),
):
    filters = []

    if bias:
        filters.append(Signal.bias == bias.upper())
    if min_dip_score > 0:
        filters.append(Signal.dip_score >= min_dip_score)
    if min_confidence > 0:
        filters.append(Signal.confidence >= min_confidence)
    if sentiment:
        filters.append(Signal.sentiment_label == sentiment.lower())

    sort_col = getattr(Signal, sort_by, Signal.composite_score)

    query = (
        select(Signal, Ticker.name, Ticker.sector)
        .join(Ticker, Signal.symbol == Ticker.symbol, isouter=True)
        .where(and_(*filters) if filters else True)
        .order_by(desc(sort_col))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.fetchall()

    # Count query
    count_q = select(Signal).where(and_(*filters) if filters else True)
    count_result = await db.execute(count_q)
    total = len(count_result.fetchall())

    signals = []
    for row in rows:
        sig = row[0]
        name = row[1]
        sector_val = row[2]
        s = SignalResponse.model_validate(sig)
        s.name = name
        s.sector = sector_val

        # Flatten nested objects
        from models.schemas import TradeLevels, TechnicalSnapshot, NewsItem
        s.levels = TradeLevels(
            entry_low=sig.entry_low,
            entry_high=sig.entry_high,
            stop_loss=sig.stop_loss,
            target_1=sig.target_1,
            target_2=sig.target_2,
            risk_reward=sig.risk_reward,
        )
        s.technicals = TechnicalSnapshot(
            price=sig.price,
            rsi_14=sig.rsi_14,
            bb_pct=sig.bb_pct,
            ma20=sig.ma20,
            ma50=sig.ma50,
            ma200=sig.ma200,
            atr=sig.atr,
            zscore=sig.zscore,
        )
        if sig.news_headlines:
            s.news = [NewsItem(**h) for h in sig.news_headlines[:5]]
        signals.append(s)

    return SignalListResponse(
        signals=signals,
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/api/signals/{signal_id}", response_model=SignalResponse)
async def get_signal(signal_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    sig = result.scalar_one_or_none()
    if not sig:
        raise HTTPException(status_code=404, detail="Signal not found")
    return SignalResponse.model_validate(sig)


# ── Tickers ────────────────────────────────────────────────────────────────────

@app.get("/api/tickers/{symbol}", response_model=TickerResponse)
async def get_ticker(symbol: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Ticker).where(Ticker.symbol == symbol.upper()))
    ticker = result.scalar_one_or_none()
    if not ticker:
        raise HTTPException(status_code=404, detail="Ticker not found")
    return TickerResponse.model_validate(ticker)


@app.get("/api/tickers/{symbol}/signals")
async def get_ticker_signals(
    symbol: str,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Signal)
        .where(Signal.symbol == symbol.upper())
        .order_by(desc(Signal.ts))
        .limit(limit)
    )
    sigs = result.scalars().all()
    return [SignalResponse.model_validate(s) for s in sigs]


# ── Watchlist ──────────────────────────────────────────────────────────────────

@app.post("/api/watchlist", response_model=WatchlistResponse, status_code=201)
async def add_to_watchlist(body: WatchlistAdd, db: AsyncSession = Depends(get_db)):
    entry = Watchlist(
        name=body.name,
        symbol=body.symbol.upper(),
        notes=body.notes,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    return WatchlistResponse.model_validate(entry)


@app.get("/api/watchlist", response_model=list[WatchlistResponse])
async def list_watchlist(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Watchlist).order_by(Watchlist.added_at.desc()))
    return [WatchlistResponse.model_validate(w) for w in result.scalars().all()]


@app.delete("/api/watchlist/{entry_id}", status_code=204)
async def remove_from_watchlist(entry_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Watchlist).where(Watchlist.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    await db.delete(entry)


# ── Scan trigger ───────────────────────────────────────────────────────────────

@app.post("/api/scan/trigger")
async def trigger_scan():
    """Manually trigger a market scan (enqueues Celery task)."""
    from workers.tasks import run_full_scan
    task = run_full_scan.delay()
    return {"task_id": task.id, "status": "queued"}


# ── Market context ─────────────────────────────────────────────────────────────

@app.get("/api/market/context")
async def get_market_context():
    scanner = MarketScanner()
    ctx = await scanner._fetch_market_context()
    return {
        "spy_trend": ctx.spy_trend,
        "spy_rsi": ctx.spy_rsi,
        "spy_vs_ma200_pct": round(ctx.spy_vs_ma200_pct * 100, 2),
        "vix_level": ctx.vix_level,
        "regime": ctx.regime,
        "vix_regime": ctx.vix_regime,
    }


# ── Backtest ───────────────────────────────────────────────────────────────────

@app.post("/api/backtest", response_model=BacktestResponse)
async def run_backtest(request: BacktestRequest, db: AsyncSession = Depends(get_db)):
    """
    Run a historical backtest.

    This endpoint loads historical OHLCV data from the DB and runs the
    strategy walk-forward.  For large date ranges or universes, this may
    take several minutes — consider running as a Celery task instead.
    """
    from services.backtest import BacktestEngine
    from services.polygon_client import PolygonClient
    from datetime import date

    # Load tickers for universe
    if request.universe in ("SP500", "NASDAQ100"):
        result = await db.execute(
            select(Ticker.symbol)
            .where(
                Ticker.is_active == True,
                Ticker.market_cap >= 10_000_000_000,  # large caps for index proxy
            )
            .limit(100)
        )
        symbols = [row[0] for row in result.fetchall()]
    else:
        symbols = [s.strip().upper() for s in request.universe.split(",")]

    if not symbols:
        raise HTTPException(status_code=400, detail="No tickers in universe")

    # Fetch price data
    async with PolygonClient() as poly:
        price_data = {}
        for sym in symbols[:50]:  # cap for demo
            bars = await poly.get_daily_bars(
                sym,
                from_date=request.start_date.date() - timedelta(days=400),
                to_date=request.end_date.date(),
            )
            if not bars.empty:
                price_data[sym] = bars

    from datetime import timedelta

    engine_bt = BacktestEngine()
    result_dict = await engine_bt.run(request, price_data)

    # Persist
    bt = BacktestResult(
        strategy_params=request.model_dump(mode="json"),
        start_date=request.start_date,
        end_date=request.end_date,
        universe=request.universe,
        **{k: v for k, v in result_dict.items() if k not in ("id", "run_at")},
    )
    db.add(bt)
    await db.flush()
    await db.refresh(bt)
    return BacktestResponse.model_validate(bt)


@app.get("/api/backtest/{result_id}", response_model=BacktestResponse)
async def get_backtest(result_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BacktestResult).where(BacktestResult.id == result_id)
    )
    bt = result.scalar_one_or_none()
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest result not found")
    return BacktestResponse.model_validate(bt)


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        # Send connection confirmation
        await ws_manager.send_to(ws, {
            "type": "connected",
            "payload": {"connections": ws_manager.connection_count},
        })
        # Keep alive — client can send pings
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws_manager.send_to(ws, {"type": "pong", "payload": {}})
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ts": datetime.utcnow().isoformat(),
        "ws_connections": ws_manager.connection_count,
    }
