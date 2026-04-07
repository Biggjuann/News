"""
Alpha Vantage news sentiment source.
Free tier: 25 requests/day — use sparingly.
Returns GPT-scored sentiment per article with ticker-level granularity.
https://www.alphavantage.co/documentation/#news-sentiment
"""

import logging
from datetime import datetime, timezone

import aiohttp

from config.settings import settings
from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)


class AlphaVantageSource(NewsSource):
    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self):
        super().__init__("alpha_vantage")
        self._request_count = 0

    @property
    def is_configured(self) -> bool:
        return bool(settings.alpha_vantage_api_key)

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        if not self.is_configured:
            return []

        # Conservative: batch tickers into one request to conserve daily limit
        if self._request_count >= 20:
            logger.warning("Alpha Vantage: approaching daily limit, skipping")
            return []

        articles = []
        ticker_str = ",".join(tickers[:5])  # AV supports comma-separated, limit to 5

        async with aiohttp.ClientSession() as session:
            try:
                params = {
                    "function": "NEWS_SENTIMENT",
                    "tickers": ticker_str,
                    "sort": "LATEST",
                    "limit": 20,
                    "apikey": settings.alpha_vantage_api_key,
                }
                async with session.get(
                    self.BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Alpha Vantage returned {resp.status}")
                        return []
                    data = await resp.json()

                self._request_count += 1

                for item in data.get("feed", []):
                    # Extract ticker-specific sentiment scores
                    ticker_sentiments = {}
                    for ts in item.get("ticker_sentiment", []):
                        ticker_sentiments[ts["ticker"]] = float(ts.get("ticker_sentiment_score", 0))

                    matched_tickers = [
                        t for t in tickers
                        if t in ticker_sentiments
                    ]

                    # Use the average of matched ticker sentiments as raw score
                    raw_score = None
                    if matched_tickers:
                        scores = [ticker_sentiments[t] for t in matched_tickers]
                        raw_score = sum(scores) / len(scores)
                    elif "overall_sentiment_score" in item:
                        raw_score = float(item["overall_sentiment_score"])

                    # Parse datetime
                    pub_str = item.get("time_published", "")
                    try:
                        pub_dt = datetime.strptime(pub_str, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pub_dt = datetime.now(timezone.utc)

                    articles.append(NewsArticle(
                        source="alpha_vantage",
                        headline=item.get("title", ""),
                        summary=item.get("summary", ""),
                        url=item.get("url", ""),
                        tickers=matched_tickers if matched_tickers else [],
                        published_at=pub_dt,
                        raw_sentiment_score=raw_score,
                    ))
            except Exception as e:
                logger.error(f"Alpha Vantage fetch error: {e}")

        self._last_fetch = datetime.now(timezone.utc)
        return self._deduplicate(articles)
