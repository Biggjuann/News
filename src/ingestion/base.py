"""Base class for news source adapters."""

import abc
import logging
from datetime import datetime, timezone

from src.models import NewsArticle

logger = logging.getLogger(__name__)


class NewsSource(abc.ABC):
    """Abstract base class for all news source adapters."""

    def __init__(self, name: str):
        self.name = name
        self._seen_ids: set[str] = set()
        self._last_fetch: datetime | None = None

    @abc.abstractmethod
    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        """Fetch new articles for the given tickers. Returns only unseen articles."""
        ...

    def _deduplicate(self, articles: list[NewsArticle]) -> list[NewsArticle]:
        """Filter out articles we've already seen."""
        new_articles = []
        for article in articles:
            aid = article.article_id
            if aid not in self._seen_ids:
                self._seen_ids.add(aid)
                new_articles.append(article)
        # Keep seen set from growing unbounded
        if len(self._seen_ids) > 10_000:
            self._seen_ids = set(list(self._seen_ids)[-5_000:])
        return new_articles

    @property
    def is_configured(self) -> bool:
        """Override in subclasses that need API keys."""
        return True
