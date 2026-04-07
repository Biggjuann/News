"""
Financial Modeling Prep (FMP) news source.
Free tier available — stock news with sentiment scores per article.
https://site.financialmodelingprep.com/developer/docs#stock-news
"""

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


class FMPSource(NewsSource):
    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self):
        super().__init__("fmp")

    @property
    def is_configured(self) -> bool:
        return bool(settings.fmp_api_key)

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        if not self.is_configured:
            return []

        articles = []
        async with aiohttp.ClientSession() as session:
            try:
                # FMP stock news endpoint supports ticker filtering
                ticker_str = ",".join(tickers[:5])
                url = f"{self.BASE_URL}/stock_news"
                params = {
                    "tickers": ticker_str,
                    "limit": 30,
                    "apikey": settings.fmp_api_key,
                }
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning(f"FMP returned {resp.status}")
                        return []
                    data = await resp.json()

                for item in data:
                    sentiment_label = item.get("sentiment", "Neutral")
                    raw_score = SENTIMENT_MAP.get(sentiment_label, 0.0)

                    # Parse published date
                    pub_str = item.get("publishedDate", "")
                    try:
                        pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pub_dt = datetime.now(timezone.utc)

                    ticker = item.get("symbol", "")
                    articles.append(NewsArticle(
                        source="fmp",
                        headline=item.get("title", ""),
                        summary=item.get("text", "")[:500],
                        url=item.get("url", ""),
                        tickers=[ticker] if ticker else [],
                        published_at=pub_dt,
                        raw_sentiment_score=raw_score,
                    ))
            except Exception as e:
                logger.error(f"FMP fetch error: {e}")

        self._last_fetch = datetime.now(timezone.utc)
        return self._deduplicate(articles)
