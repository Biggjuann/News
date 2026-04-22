"""
White House official briefings and statements.

Sources:
- White House Briefing Room RSS (official)
- White House Presidential Actions RSS
- White House press pool feeds
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)

WHITEHOUSE_FEEDS = {
    "wh_briefing": "https://www.whitehouse.gov/briefing-room/feed/",
    "wh_presidential": "https://www.whitehouse.gov/presidential-actions/feed/",
    "wh_statements": "https://www.whitehouse.gov/briefing-room/statements-releases/feed/",
}


def _parse_rss(content: str) -> list[dict]:
    entries = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.debug(f"XML parse error: {e}")
        return entries

    for item in root.iter("item"):
        entries.append({
            "title": (item.findtext("title") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
        })

    return entries[:15]


def _parse_date(date_str: str) -> datetime:
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        pass
    return datetime.now(timezone.utc)


def _strip_html(text: str) -> str:
    import re
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    clean = clean.replace('&#39;', "'").replace('&quot;', '"')
    return clean.strip()


class WhiteHouseSource(NewsSource):
    def __init__(self, feed_name: str, feed_url: str):
        super().__init__(feed_name)
        self.feed_url = feed_url

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        articles = []

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.get(
                    self.feed_url,
                    headers={"User-Agent": "NewsTradingBot/1.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"{self.name}: HTTP {resp.status}")
                        return []
                    content = await resp.text()

            entries = _parse_rss(content)

            for entry in entries:
                headline = _strip_html(entry["title"])
                summary = _strip_html(entry["description"])

                if not headline:
                    continue

                articles.append(NewsArticle(
                    source=self.name,
                    headline=f"[White House] {headline}",
                    summary=summary[:500],
                    url=entry["link"],
                    tickers=["SPY"],
                    published_at=_parse_date(entry["published"]),
                ))

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"{self.name} fetch error: {e}")

        return self._deduplicate(articles)


def create_whitehouse_sources() -> list[WhiteHouseSource]:
    return [WhiteHouseSource(name, url) for name, url in WHITEHOUSE_FEEDS.items()]
