"""
RSS feed ingestion for free news sources.
No sentiment scoring — headlines will be scored by the LLM layer.

Sources:
- Google News RSS (financial topics)
- MarketWatch RSS
- SEC EDGAR RSS (8-K filings)
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)

RSS_FEEDS = {
    "google_finance": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB",
    "marketwatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "sec_edgar_8k": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=20&search_text=&action=getcurrent&output=atom",
}

# Atom namespace used by SEC EDGAR
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _parse_rss(content: str) -> list[dict]:
    """Parse RSS 2.0 or Atom feed XML into a list of entry dicts."""
    entries = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.warning(f"XML parse error: {e}")
        return entries

    # RSS 2.0: <rss><channel><item>...
    for item in root.iter("item"):
        entries.append({
            "title": (item.findtext("title") or "").strip(),
            "summary": (item.findtext("description") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
        })

    # Atom: <feed><entry>...
    if not entries:
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            entries.append({
                "title": (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip(),
                "summary": (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip(),
                "link": link_el.get("href", "") if link_el is not None else "",
                "published": (entry.findtext("{http://www.w3.org/2005/Atom}updated") or "").strip(),
            })

    return entries[:20]


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

            entries = _parse_rss(content)

            for entry in entries:
                headline = entry["title"]
                summary = entry["summary"]
                link = entry["link"]

                # Try to parse publish date
                pub_dt = datetime.now(timezone.utc)
                if entry["published"]:
                    try:
                        pub_dt = parsedate_to_datetime(entry["published"])
                    except Exception:
                        try:
                            # Atom dates are ISO format
                            pub_dt = datetime.fromisoformat(
                                entry["published"].replace("Z", "+00:00")
                            )
                        except Exception:
                            pass

                # Match tickers mentioned in headline/summary
                text = f" {headline} {summary} ".upper()
                matched_tickers = [t for t in tickers if f" {t.upper()} " in text]

                articles.append(NewsArticle(
                    source=self.name,
                    headline=headline,
                    summary=summary[:500] if summary else "",
                    url=link,
                    tickers=matched_tickers,
                    published_at=pub_dt,
                    raw_sentiment_score=None,
                ))

        except Exception as e:
            logger.error(f"RSS {self.name} fetch error: {e}")

        return self._deduplicate(articles)


def create_rss_sources() -> list[RSSSource]:
    """Create all configured RSS source adapters."""
    return [RSSSource(name, url) for name, url in RSS_FEEDS.items()]
