"""
Direct Truth Social API access (Mastodon-compatible endpoints).

Trump's account ID: 107780257626128497
API endpoint: https://truthsocial.com/api/v1/accounts/{id}/statuses

This bypasses RSS bridges which are often rate-limited or blocked.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)

TRUMP_ACCOUNT_ID = "107780257626128497"
TRUTH_SOCIAL_BASE = "https://truthsocial.com"

# User agent that works with Truth Social
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _strip_html(html: str) -> str:
    """Strip HTML and clean up text."""
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'</p>\s*<p>', '\n\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&#39;', "'").replace('&quot;', '"').replace('&nbsp;', ' ')
    return text.strip()


class TruthSocialAPISource(NewsSource):
    """Direct Truth Social API integration for @realDonaldTrump posts."""

    def __init__(self):
        super().__init__("truth_social_api")

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        articles = []
        url = f"{TRUTH_SOCIAL_BASE}/api/v1/accounts/{TRUMP_ACCOUNT_ID}/statuses"
        params = {"limit": 20, "exclude_replies": "true"}

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            ) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning(f"Truth Social API: HTTP {resp.status}")
                        return []

                    data = await resp.json()

                if not isinstance(data, list):
                    logger.warning(f"Truth Social API: unexpected response type {type(data)}")
                    return []

                for post in data:
                    # Skip reblogs and replies (already excluded but double check)
                    if post.get("in_reply_to_id"):
                        continue

                    content = post.get("content", "")
                    if not content:
                        continue

                    text = _strip_html(content)
                    if not text:
                        continue

                    # Parse timestamp
                    created_at = post.get("created_at", "")
                    try:
                        pub_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pub_dt = datetime.now(timezone.utc)

                    post_id = post.get("id", "")
                    post_url = post.get("url", f"{TRUTH_SOCIAL_BASE}/@realDonaldTrump/{post_id}")

                    # Headline is the first 200 chars of the post
                    headline = text[:200]
                    if len(text) > 200:
                        headline += "..."

                    articles.append(NewsArticle(
                        source="trump_truth_social",
                        headline=f"[Trump Truth] {headline}",
                        summary=text[:1000],
                        url=post_url,
                        tickers=["SPY"],
                        published_at=pub_dt,
                    ))

            if articles:
                logger.info(f"Truth Social API: fetched {len(articles)} Trump posts")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Truth Social API fetch error: {e}")

        return self._deduplicate(articles)
