"""
Google News RSS filtered for political/Trump/White House topics.

Google News RSS is free, reliable, and doesn't require API keys.
We use targeted search queries to get Trump and White House news
that could move SPY.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)

# Google News RSS search queries for political market movers
POLITICAL_SEARCH_QUERIES = {
    "trump_market": "Trump+tariff+OR+trade+OR+market+OR+economy+OR+executive+order",
    "trump_truth": "Trump+Truth+Social+post",
    "white_house": "White+House+statement+OR+briefing+OR+executive+order",
    "trade_policy": "tariff+OR+trade+war+OR+sanctions+China+OR+EU",
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def _parse_rss(content: str) -> list[dict]:
    entries = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return entries

    for item in root.iter("item"):
        entries.append({
            "title": (item.findtext("title") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
            "source": (item.findtext("source") or "").strip(),
        })

    return entries[:15]


def _parse_date(date_str: str) -> datetime:
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return datetime.now(timezone.utc)


def _strip_html(text: str) -> str:
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    clean = clean.replace('&#39;', "'").replace('&quot;', '"')
    return clean.strip()


class GoogleNewsPoliticalSource(NewsSource):
    def __init__(self, query_name: str, query: str):
        super().__init__(f"google_news_{query_name}")
        self.feed_url = GOOGLE_NEWS_RSS.format(query=query)

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
                news_source = entry.get("source", "")

                if not headline:
                    continue

                # Prepend source outlet if available
                source_tag = f"[{news_source}] " if news_source else ""

                articles.append(NewsArticle(
                    source=self.name,
                    headline=f"{source_tag}{headline}",
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


def create_google_political_sources() -> list[GoogleNewsPoliticalSource]:
    return [
        GoogleNewsPoliticalSource(name, query)
        for name, query in POLITICAL_SEARCH_QUERIES.items()
    ]
