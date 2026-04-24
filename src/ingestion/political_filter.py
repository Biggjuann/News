"""
Political news filter for SPY signals.

Wraps any news source and filters articles for political/Trump/White House
keywords relevant to market-moving events.
"""

import logging
from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)

# Keywords that indicate market-moving political news
POLITICAL_KEYWORDS = [
    # Trump / administration
    "trump", "potus", "president", "white house", "oval office",
    "truth social", "mar-a-lago",

    # Key administration figures
    "vance", "rubio", "bessent", "lutnick",

    # Policy keywords that move markets
    "tariff", "trade war", "trade deal", "sanctions", "executive order",
    "deregulat", "tax cut", "tax hike", "government shutdown",
    "debt ceiling", "deficit", "spending bill",

    # Geopolitics that move SPY
    "ceasefire", "peace deal", "military action", "war",
    "iran", "china", "russia", "ukraine", "nato",
    "nuclear", "missile", "invasion",

    # Economic policy
    "federal reserve", "fed chair", "powell", "rate cut", "rate hike",
    "inflation", "recession", "gdp", "jobs report", "unemployment",
    "stimulus", "bailout",

    # Market-specific political
    "s&p", "spy", "dow", "nasdaq", "stock market", "wall street",
    "market crash", "market rally", "bull market", "bear market",
]


def is_political_news(article: NewsArticle) -> bool:
    """Check if an article is political/policy news that could move SPY."""
    text = f"{article.headline} {article.summary}".lower()
    return any(kw in text for kw in POLITICAL_KEYWORDS)


class PoliticalFilter(NewsSource):
    """Wraps a news source and only passes through political/policy articles."""

    def __init__(self, inner_source: NewsSource):
        super().__init__(f"political_{inner_source.name}")
        self._inner = inner_source

    @property
    def is_configured(self) -> bool:
        return self._inner.is_configured

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        # Fetch from the inner source using broad tickers for general news
        # but then filter for political content and retag as SPY
        broad_tickers = ["SPY", "QQQ", "DIA"]
        articles = await self._inner.fetch(broad_tickers)

        political = []
        for article in articles:
            if is_political_news(article):
                article.tickers = ["SPY"]  # retag everything as SPY
                political.append(article)

        if political:
            logger.info(
                f"{self.name}: {len(political)}/{len(articles)} articles matched political filter"
            )

        return self._deduplicate(political)
