"""
Trump Truth Social feed ingestion.

Uses RSS bridge services to get Trump's Truth Social posts.
Falls back to multiple bridge providers for reliability.
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

# RSS bridge URLs for Truth Social — try multiple for reliability
TRUTH_SOCIAL_FEEDS = [
    "https://rsshub.app/truthsocial/user/realDonaldTrump",
    "https://rss.app/feeds/v1.1/ts-realDonaldTrump.xml",
]


def _parse_rss(content: str) -> list[dict]:
    """Parse RSS 2.0 or Atom feed XML into entry dicts."""
    entries = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.warning(f"XML parse error: {e}")
        return entries

    # RSS 2.0
    for item in root.iter("item"):
        entries.append({
            "title": (item.findtext("title") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
        })

    # Atom
    if not entries:
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            content_el = entry.find("{http://www.w3.org/2005/Atom}content")
            entries.append({
                "title": (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip(),
                "description": (content_el.text if content_el is not None else "").strip(),
                "link": link_el.get("href", "") if link_el is not None else "",
                "published": (entry.findtext("{http://www.w3.org/2005/Atom}updated") or "").strip(),
            })

    return entries[:20]


def _parse_date(date_str: str) -> datetime:
    """Try multiple date formats."""
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
    """Remove HTML tags from text."""
    import re
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    clean = clean.replace('&#39;', "'").replace('&quot;', '"')
    return clean.strip()


class TruthSocialSource(NewsSource):
    def __init__(self):
        super().__init__("truth_social")

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        articles = []

        for feed_url in TRUTH_SOCIAL_FEEDS:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                    async with session.get(
                        feed_url,
                        headers={"User-Agent": "NewsTradingBot/1.0"},
                    ) as resp:
                        if resp.status != 200:
                            logger.debug(f"Truth Social feed {feed_url}: {resp.status}")
                            continue
                        content = await resp.text()

                entries = _parse_rss(content)
                if not entries:
                    continue

                for entry in entries:
                    # Use description as the main text (Truth posts don't have titles)
                    text = _strip_html(entry["description"] or entry["title"])
                    headline = text[:200] if text else entry["title"]

                    if not headline:
                        continue

                    articles.append(NewsArticle(
                        source="trump_truth_social",
                        headline=headline,
                        summary=text[:500],
                        url=entry["link"],
                        tickers=["SPY"],  # all Trump posts affect SPY
                        published_at=_parse_date(entry["published"]),
                    ))

                if articles:
                    logger.info(f"Truth Social: fetched {len(articles)} posts")
                    break  # got data from this feed, don't try others

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Truth Social feed {feed_url} error: {e}")
                continue

        return self._deduplicate(articles)
