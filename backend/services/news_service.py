"""
News Service
============

Aggregates financial news from multiple sources:
- NewsAPI (general financial news)
- Benzinga (premium financial news with tickers)
- RSS fallbacks (Yahoo Finance, Seeking Alpha)

All articles are normalised to a common schema before being passed
to the sentiment engine.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
import xml.etree.ElementTree as ET

import aiohttp
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import settings

logger = structlog.get_logger(__name__)

# ── Normalised article schema ──────────────────────────────────────────────────
def make_article(title: str, url: str = "", source: str = "", published_at: str = "") -> dict:
    return {
        "id":           hashlib.md5(f"{url}{title}".encode()).hexdigest()[:12],
        "title":        title.strip(),
        "url":          url,
        "source":       source,
        "published_at": published_at,
    }


class NewsService:
    """Async news aggregator.  Use as async context manager."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "NewsService":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._session:
            await self._session.close()

    async def get_news_for_ticker(
        self,
        symbol: str,
        hours_back: int = 72,
    ) -> list[dict]:
        """Gather news from all available sources for a single ticker."""
        tasks = [
            self._fetch_newsapi(symbol, hours_back),
            self._fetch_benzinga(symbol, hours_back),
            self._fetch_yahoo_rss(symbol),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        articles: list[dict] = []
        seen_ids: set[str] = set()

        for batch in results:
            if isinstance(batch, Exception):
                logger.warning("news_source_failed", error=str(batch))
                continue
            for art in batch:
                if art["id"] not in seen_ids:
                    seen_ids.add(art["id"])
                    articles.append(art)

        # Sort by recency
        articles.sort(key=lambda a: a.get("published_at", ""), reverse=True)
        logger.info("news_fetched", symbol=symbol, count=len(articles))
        return articles

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
    async def _fetch_newsapi(self, symbol: str, hours_back: int) -> list[dict]:
        if not settings.NEWS_API_KEY:
            return []
        from_dt = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "q":        f'"{symbol}" stock',
            "from":     from_dt,
            "language": "en",
            "sortBy":   "publishedAt",
            "pageSize": 20,
            "apiKey":   settings.NEWS_API_KEY,
        }
        async with self._session.get("https://newsapi.org/v2/everything", params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        articles = []
        for a in data.get("articles", []):
            articles.append(make_article(
                title=a.get("title", ""),
                url=a.get("url", ""),
                source=a.get("source", {}).get("name", "NewsAPI"),
                published_at=a.get("publishedAt", ""),
            ))
        return articles

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
    async def _fetch_benzinga(self, symbol: str, hours_back: int) -> list[dict]:
        if not settings.BENZINGA_API_KEY:
            return []
        from_dt = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime("%Y-%m-%d")
        params = {
            "token":    settings.BENZINGA_API_KEY,
            "tickers":  symbol,
            "dateFrom": from_dt,
            "pageSize": 20,
        }
        async with self._session.get(
            "https://api.benzinga.com/api/v2/news",
            params=params,
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()

        articles = []
        for a in data:
            articles.append(make_article(
                title=a.get("title", ""),
                url=a.get("url", ""),
                source="Benzinga",
                published_at=a.get("created", ""),
            ))
        return articles

    async def _fetch_yahoo_rss(self, symbol: str) -> list[dict]:
        """Yahoo Finance RSS — no API key required."""
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    return []
                content = await resp.text()
        except Exception:
            return []

        articles = []
        try:
            root = ET.fromstring(content)
            ns = {"dc": "http://purl.org/dc/elements/1.1/"}
            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el  = item.find("link")
                date_el  = item.find("pubDate")
                if title_el is not None:
                    articles.append(make_article(
                        title=title_el.text or "",
                        url=link_el.text if link_el is not None else "",
                        source="Yahoo Finance",
                        published_at=date_el.text if date_el is not None else "",
                    ))
        except ET.ParseError:
            pass

        return articles
