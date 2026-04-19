"""
Celery Tasks
============

All async scanning tasks are dispatched through Celery.

Schedule (set in beat config below):
- full_market_scan:    every SCAN_INTERVAL_SECONDS (default 300s)
- watchlist_scan:      every WATCHLIST_SCAN_SECONDS (default 60s)
- refresh_universe:    daily at 09:00 UTC (before market open)
- refresh_fundamentals: daily at 20:00 UTC (after market close)
"""

import asyncio
import structlog
from celery import Celery
from celery.schedules import crontab

from core.config import settings

logger = structlog.get_logger(__name__)

celery_app = Celery(
    "quant_platform",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,   # one task at a time per worker — prevents memory bloat
    task_acks_late=True,            # don't ack until task completes
    task_reject_on_worker_lost=True,
)

# ── Beat schedule ──────────────────────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    "full-market-scan": {
        "task":     "workers.tasks.run_full_scan",
        "schedule": settings.SCAN_INTERVAL_SECONDS,
    },
    "watchlist-scan": {
        "task":     "workers.tasks.run_watchlist_scan",
        "schedule": settings.WATCHLIST_SCAN_SECONDS,
    },
    "refresh-universe": {
        "task":     "workers.tasks.refresh_ticker_universe",
        "schedule": crontab(hour=9, minute=0),
    },
    "refresh-fundamentals": {
        "task":     "workers.tasks.refresh_fundamentals",
        "schedule": crontab(hour=20, minute=0),
    },
}


# ── Task definitions ───────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="workers.tasks.run_full_scan",
    max_retries=2,
    default_retry_delay=60,
)
def run_full_scan(self):
    """Scan entire configured universe and generate signals."""
    from services.scanner import MarketScanner
    from services.ws_manager import ws_manager

    async def _inner():
        scanner = MarketScanner()
        signals = await scanner.run_full_scan()
        # Broadcast to all connected WebSocket clients
        await ws_manager.broadcast({
            "type": "scan_complete",
            "payload": {
                "signal_count": len(signals),
                "top_signals": sorted(
                    signals, key=lambda s: s.get("composite_score", 0), reverse=True
                )[:10],
            },
        })
        logger.info("full_scan_task_done", signals=len(signals))
        return len(signals)

    try:
        return asyncio.run(_inner())
    except Exception as exc:
        logger.error("full_scan_task_failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    name="workers.tasks.run_watchlist_scan",
    max_retries=2,
    default_retry_delay=30,
)
def run_watchlist_scan(self):
    """Refresh signals for watchlisted tickers only (faster)."""
    from services.scanner import MarketScanner
    from db.session import AsyncSessionFactory
    from models.orm import Watchlist
    from sqlalchemy import select

    async def _inner():
        async with AsyncSessionFactory() as db:
            result = await db.execute(select(Watchlist.symbol).distinct())
            symbols = [row[0] for row in result.fetchall()]

        if not symbols:
            return 0

        scanner = MarketScanner(dip_threshold=30.0)  # lower threshold for watchlist
        signals = await scanner.run_full_scan(symbols=symbols)
        logger.info("watchlist_scan_done", symbols=len(symbols), signals=len(signals))
        return len(signals)

    try:
        return asyncio.run(_inner())
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(name="workers.tasks.refresh_ticker_universe")
def refresh_ticker_universe():
    """Refresh the tickers table from Polygon reference data."""
    from services.polygon_client import PolygonClient
    from db.session import AsyncSessionFactory
    from models.orm import Ticker
    from sqlalchemy.dialects.postgresql import insert

    async def _inner():
        async with PolygonClient() as poly:
            raw_tickers = await poly.get_tickers()

        async with AsyncSessionFactory() as db:
            for t in raw_tickers:
                cap  = t.get("market_cap", 0) or 0
                stmt = insert(Ticker).values(
                    symbol=t.get("ticker", ""),
                    name=t.get("name", ""),
                    sector=t.get("sic_description", ""),
                    exchange=t.get("primary_exchange", ""),
                    market_cap=int(cap),
                    is_active=t.get("active", True),
                ).on_conflict_do_update(
                    index_elements=["symbol"],
                    set_={
                        "name":      t.get("name", ""),
                        "market_cap": int(cap),
                        "is_active": t.get("active", True),
                    }
                )
                await db.execute(stmt)
            await db.commit()
        logger.info("universe_refreshed", count=len(raw_tickers))

    asyncio.run(_inner())


@celery_app.task(name="workers.tasks.refresh_fundamentals")
def refresh_fundamentals():
    """Refresh fundamental data from FMP for all active tickers."""
    from services.fmp_client import FMPClient
    from db.session import AsyncSessionFactory
    from models.orm import Ticker, Fundamental
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    async def _inner():
        async with AsyncSessionFactory() as db:
            result = await db.execute(
                select(Ticker.symbol).where(Ticker.is_active == True)
            )
            symbols = [row[0] for row in result.fetchall()]

        async with FMPClient() as fmp:
            for symbol in symbols[:200]:    # batch limit
                try:
                    data = await fmp.get_fundamentals(symbol)
                    if data:
                        async with AsyncSessionFactory() as db:
                            stmt = insert(Fundamental).values(
                                symbol=symbol, **data
                            ).on_conflict_do_update(
                                index_elements=["symbol"],
                                set_=data
                            )
                            await db.execute(stmt)
                            await db.commit()
                except Exception as e:
                    logger.debug("fundamental_fetch_failed", symbol=symbol, error=str(e))

        logger.info("fundamentals_refreshed", count=min(len(symbols), 200))

    asyncio.run(_inner())
