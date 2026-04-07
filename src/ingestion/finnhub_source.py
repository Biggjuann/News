"""
Finnhub news source adapter.
Free tier: 60 API calls/minute, real-time company news with sentiment.
https://finnhub.io/docs/api/company-news
"""

import logging
from datetime import datetime, timezone, timedelta

import aiohttp

from config.settings import settings
from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)


class FinnhubSource(NewsSource):
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self):
        super().__init__("finnhub")

    @property
    def is_configured(self) -> bool:
        return bool(settings.finnhub_api_key)

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        if not self.is_configured:
            return []

        articles = []
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(hours=1)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")

        async with aiohttp.ClientSession() as session:
            for ticker in tickers:
                try:
                    url = f"{self.BASE_URL}/company-news"
                    params = {
                        "symbol": ticker,
                        "from": date_from,
                        "to": date_to,
                        "token": settings.finnhub_api_key,
                    }
                    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            logger.warning(f"Finnhub returned {resp.status} for {ticker}")
                            continue
                        data = await resp.json()

                    for item in data[:10]:  # limit to most recent 10 per ticker
                        articles.append(NewsArticle(
                            source="finnhub",
                            headline=item.get("headline", ""),
                            summary=item.get("summary", ""),
                            url=item.get("url", ""),
                            tickers=[ticker],
                            published_at=datetime.fromtimestamp(
                                item.get("datetime", 0), tz=timezone.utc
                            ),
                        ))
                except Exception as e:
                    logger.error(f"Finnhub fetch error for {ticker}: {e}")

        self._last_fetch = now
        return self._deduplicate(articles)


class FinnhubSentimentSource(NewsSource):
    """Fetches Finnhub's overall market news sentiment (not per-ticker)."""
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self):
        super().__init__("finnhub_sentiment")

    @property
    def is_configured(self) -> bool:
        return bool(settings.finnhub_api_key)

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        if not self.is_configured:
            return []

        articles = []
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.BASE_URL}/news"
                params = {
                    "category": "general",
                    "token": settings.finnhub_api_key,
                }
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Finnhub general news returned {resp.status}")
                        return []
                    data = await resp.json()

                for item in data[:20]:
                    related = item.get("related", "")
                    matched_tickers = [
                        t for t in tickers
                        if t.upper() in related.upper()
                    ] if related else []

                    articles.append(NewsArticle(
                        source="finnhub_general",
                        headline=item.get("headline", ""),
                        summary=item.get("summary", ""),
                        url=item.get("url", ""),
                        tickers=matched_tickers,
                        published_at=datetime.fromtimestamp(
                            item.get("datetime", 0), tz=timezone.utc
                        ),
                    ))
            except Exception as e:
                logger.error(f"Finnhub general news fetch error: {e}")

        return self._deduplicate(articles)
