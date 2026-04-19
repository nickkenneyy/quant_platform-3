"""
Polygon.io Data Service
=======================

Handles all market data ingestion:
- Daily + intraday OHLCV bars
- Live price quotes (REST polling, WebSocket streaming for watchlist)
- Ticker universe (all US equities passing liquidity filter)

Rate limit: Polygon free tier = 5 calls/min, paid = unlimited.
We use tenacity for retry/backoff and aiohttp for async HTTP.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import AsyncGenerator, Optional

import aiohttp
import pandas as pd
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from core.config import settings

logger = structlog.get_logger(__name__)

POLYGON_BASE = "https://api.polygon.io"


class PolygonClient:
    """Async Polygon.io REST client with automatic retry and rate limiting."""

    def __init__(self, api_key: Optional[str] = None):
        self._key = api_key or settings.POLYGON_API_KEY
        self._session: Optional[aiohttp.ClientSession] = None
        # Leaky-bucket: Polygon paid plan allows ~100 req/s
        self._semaphore = asyncio.Semaphore(10)

    async def __aenter__(self) -> "PolygonClient":
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self._key}"},
            timeout=aiohttp.ClientTimeout(total=30),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._session:
            await self._session.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def _get(self, path: str, params: dict | None = None) -> dict:
        """Execute a GET request with retry."""
        url = f"{POLYGON_BASE}{path}"
        async with self._semaphore:
            async with self._session.get(url, params=params or {}) as resp:
                if resp.status == 429:
                    logger.warning("polygon_rate_limited", path=path)
                    await asyncio.sleep(60)
                    raise aiohttp.ClientError("Rate limited")
                resp.raise_for_status()
                return await resp.json()

    async def get_tickers(
        self,
        market: str = "stocks",
        active: bool = True,
        limit: int = 1000,
    ) -> list[dict]:
        """Fetch paginated list of US stock tickers."""
        tickers = []
        cursor = None
        while True:
            params = {
                "market": market,
                "active": str(active).lower(),
                "limit": limit,
            }
            if cursor:
                params["cursor"] = cursor
            data = await self._get("/v3/reference/tickers", params)
            results = data.get("results", [])
            tickers.extend(results)
            cursor = data.get("next_url", "").split("cursor=")[-1] if "next_url" in data else None
            if not cursor:
                break
            await asyncio.sleep(0.1)  # gentle pacing
        logger.info("tickers_fetched", count=len(tickers))
        return tickers

    async def get_daily_bars(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV bars for a single ticker.
        Returns a DataFrame indexed by UTC datetime.
        """
        path = f"/v2/aggs/ticker/{symbol}/range/1/day/{from_date}/{to_date}"
        params = {
            "adjusted": str(adjusted).lower(),
            "sort": "asc",
            "limit": 50000,
        }
        data = await self._get(path, params)
        results = data.get("results", [])
        if not results:
            return pd.DataFrame()

        df = pd.DataFrame(results)
        df["ts"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.rename(columns={
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
            "vw": "vwap",
            "n": "num_trades",
        })
        df = df.set_index("ts")[["open", "high", "low", "close", "volume", "vwap", "num_trades"]]
        return df

    async def get_intraday_bars(
        self,
        symbol: str,
        multiplier: int = 5,
        timespan: str = "minute",
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> pd.DataFrame:
        """Fetch intraday bars (default 5-minute)."""
        if from_date is None:
            from_date = date.today() - timedelta(days=1)
        if to_date is None:
            to_date = date.today()
        path = (
            f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/"
            f"{from_date}/{to_date}"
        )
        data = await self._get(path, {"adjusted": "true", "sort": "asc", "limit": 50000})
        results = data.get("results", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df["ts"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume","vw":"vwap"})
        return df.set_index("ts")[["open","high","low","close","volume","vwap"]]

    async def get_snapshot(self, symbol: str) -> dict:
        """Real-time price snapshot for a single ticker."""
        data = await self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}")
        return data.get("ticker", {})

    async def get_snapshots_bulk(self, symbols: list[str]) -> dict[str, dict]:
        """
        Batch snapshot for up to 250 tickers.
        Returns dict keyed by symbol.
        """
        chunk_size = 250
        result: dict[str, dict] = {}
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i: i + chunk_size]
            tickers_param = ",".join(chunk)
            data = await self._get(
                "/v2/snapshot/locale/us/markets/stocks/tickers",
                {"tickers": tickers_param},
            )
            for item in data.get("tickers", []):
                result[item["ticker"]] = item
            await asyncio.sleep(0.05)
        return result

    async def get_ticker_details(self, symbol: str) -> dict:
        """Fetch reference data for a single ticker (market cap, description, etc.)."""
        data = await self._get(f"/v3/reference/tickers/{symbol}")
        return data.get("results", {})

    async def stream_quotes(
        self,
        symbols: list[str],
    ) -> AsyncGenerator[dict, None]:
        """
        WebSocket stream of real-time quotes.
        Yields raw message dicts.

        Usage:
            async for msg in client.stream_quotes(["AAPL", "MSFT"]):
                process(msg)
        """
        ws_url = "wss://socket.polygon.io/stocks"
        import websockets
        async with websockets.connect(ws_url) as ws:
            # Authenticate
            await ws.send(f'{{"action":"auth","params":"{self._key}"}}')
            auth_msg = await ws.recv()
            logger.info("polygon_ws_auth", msg=auth_msg)

            # Subscribe
            subs = ",".join(f"Q.{s}" for s in symbols)
            await ws.send(f'{{"action":"subscribe","params":"{subs}"}}')

            async for message in ws:
                import json
                for event in json.loads(message):
                    if event.get("ev") == "Q":
                        yield event
