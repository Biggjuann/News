"""
RSS feed ingestion for free news sources.
No sentiment scoring — headlines will be scored by the LLM layer.

Sources:
- Google News RSS (financial topics)
- MarketWatch RSS
- SEC EDGAR RSS (8-K filings)
"""

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp
import feedparser

from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)

RSS_FEEDS = {
    "google_finance": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB",
    "marketwatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "sec_edgar_8k": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=20&search_text=&action=getcurrent&output=atom",
}


class RSSSource(NewsSource):
    def __init__(self, feed_name: str, feed_url: str):
        super().__init__(f"rss_{feed_name}")
        self.feed_url = feed_url

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        articles = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.feed_url,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": "NewsTradingBot/1.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"RSS {self.name} returned {resp.status}")
                        return []
                    content = await resp.text()

            feed = feedparser.parse(content)

            for entry in feed.entries[:20]:
                headline = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                link = entry.get("link", "")

                # Try to parse publish date
                pub_dt = datetime.now(timezone.utc)
                if "published" in entry:
                    try:
                        pub_dt = parsedate_to_datetime(entry.published)
                    except Exception:
                        pass

                # Match tickers mentioned in headline/summary
                text = f"{headline} {summary}".upper()
                matched_tickers = [t for t in tickers if f" {t.upper()} " in f" {text} "]

                articles.append(NewsArticle(
                    source=self.name,
                    headline=headline,
                    summary=summary[:500] if summary else "",
                    url=link,
                    tickers=matched_tickers,
                    published_at=pub_dt,
                    raw_sentiment_score=None,  # RSS feeds have no pre-scoring
                ))

        except Exception as e:
            logger.error(f"RSS {self.name} fetch error: {e}")

        return self._deduplicate(articles)


def create_rss_sources() -> list[RSSSource]:
    """Create all configured RSS source adapters."""
    return [RSSSource(name, url) for name, url in RSS_FEEDS.items()]
