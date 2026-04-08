"""
Financial Modeling Prep (FMP) news source.
Free tier available — stock news with sentiment scores per article.
Uses the v4 endpoint (v3 was deprecated for new users after Aug 2025).
https://site.financialmodelingprep.com/developer/docs
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from config.settings import settings
from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)

# FMP sentiment label → numeric score mapping
SENTIMENT_MAP = {
    "Bullish": 0.7,
    "Somewhat-Bullish": 0.4,
    "Neutral": 0.0,
    "Somewhat-Bearish": -0.4,
    "Bearish": -0.7,
}

# Try multiple FMP endpoints — v4 first, then v3 fallback
FMP_NEWS_ENDPOINTS = [
    "https://financialmodelingprep.com/stable/news/stock",
    "https://financialmodelingprep.com/api/v4/stock_news_sentiments_bulk_download",
    "https://financialmodelingprep.com/api/v3/stock_news",
]


class FMPSource(NewsSource):
    def __init__(self):
        super().__init__("fmp")
        self._working_endpoint: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(settings.fmp_api_key)

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        if not self.is_configured:
            return []

        articles = []
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                data = await self._fetch_news(session, tickers)
                if data is None:
                    return []

                for item in data:
                    sentiment_label = item.get("sentiment", "Neutral")
                    raw_score = SENTIMENT_MAP.get(sentiment_label, 0.0)

                    pub_str = item.get("publishedDate", "")
                    try:
                        pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pub_dt = datetime.now(timezone.utc)

                    ticker = item.get("symbol", item.get("ticker", ""))
                    articles.append(NewsArticle(
                        source="fmp",
                        headline=item.get("title", ""),
                        summary=item.get("text", item.get("snippet", ""))[:500],
                        url=item.get("url", ""),
                        tickers=[ticker] if ticker else [],
                        published_at=pub_dt,
                        raw_sentiment_score=raw_score,
                    ))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"FMP fetch error: {e}")

        self._last_fetch = datetime.now(timezone.utc)
        return self._deduplicate(articles)

    async def _fetch_news(self, session, tickers: list[str]) -> list | None:
        """Try FMP endpoints until one works."""
        ticker_str = ",".join(tickers[:5])

        # If we already found a working endpoint, use it
        if self._working_endpoint:
            return await self._try_endpoint(session, self._working_endpoint, ticker_str)

        # Try each endpoint
        for endpoint in FMP_NEWS_ENDPOINTS:
            result = await self._try_endpoint(session, endpoint, ticker_str)
            if result is not None:
                self._working_endpoint = endpoint
                logger.info(f"FMP: using endpoint {endpoint}")
                return result

        logger.warning("FMP: all endpoints failed")
        return None

    async def _try_endpoint(self, session, endpoint: str, ticker_str: str) -> list | None:
        """Try a single FMP endpoint."""
        params = {
            "tickers": ticker_str,
            "limit": 30,
            "apikey": settings.fmp_api_key,
        }
        try:
            async with session.get(endpoint, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    if "Legacy" in text or resp.status == 401:
                        logger.debug(f"FMP endpoint {endpoint}: deprecated or unauthorized")
                        return None
                    logger.warning(f"FMP {endpoint} returned {resp.status}")
                    return None
                data = await resp.json()
                if isinstance(data, dict) and "Error" in str(data):
                    return None
                return data if isinstance(data, list) else []
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"FMP endpoint {endpoint} error: {e}")
            return None
