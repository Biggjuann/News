"""
Trump X (Twitter) feed ingestion.

Uses RSS bridge services to get @realDonaldTrump posts.
Also monitors @POTUS and @WhiteHouse accounts.
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

# X/Twitter accounts to monitor via RSS bridges
X_ACCOUNTS = {
    "realDonaldTrump": "Trump",
    "POTUS": "POTUS",
    "WhiteHouse": "White House",
}

# RSS bridge providers — multiple for redundancy
RSS_BRIDGES = [
    "https://rsshub.app/twitter/user/{account}",
    "https://nitter.privacydev.net/{account}/rss",
    "https://twiiit.com/{account}/rss",
]


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
        })

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

    return entries[:10]


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


class XSource(NewsSource):
    def __init__(self):
        super().__init__("x_twitter")
        self._working_bridges: dict[str, str] = {}

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        articles = []

        for account, label in X_ACCOUNTS.items():
            posts = await self._fetch_account(account, label)
            articles.extend(posts)

        return self._deduplicate(articles)

    async def _fetch_account(self, account: str, label: str) -> list[NewsArticle]:
        """Fetch posts for a single X account."""
        articles = []

        # Use cached working bridge if we have one
        if account in self._working_bridges:
            bridges = [self._working_bridges[account]]
        else:
            bridges = [b.format(account=account) for b in RSS_BRIDGES]

        for feed_url in bridges:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                    async with session.get(
                        feed_url,
                        headers={"User-Agent": "NewsTradingBot/1.0"},
                    ) as resp:
                        if resp.status != 200:
                            continue
                        content = await resp.text()

                entries = _parse_rss(content)
                if not entries:
                    continue

                self._working_bridges[account] = feed_url

                for entry in entries:
                    text = _strip_html(entry["description"] or entry["title"])
                    headline = text[:200] if text else entry["title"]

                    if not headline:
                        continue

                    articles.append(NewsArticle(
                        source=f"x_{account}",
                        headline=f"[{label}] {headline}",
                        summary=text[:500],
                        url=entry["link"],
                        tickers=["SPY"],
                        published_at=_parse_date(entry["published"]),
                    ))

                if articles:
                    break

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"X feed {feed_url} error: {e}")
                continue

        return articles
