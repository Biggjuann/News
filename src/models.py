"""Shared data models for the news trading signal system."""

from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field


class Sentiment(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class Impact(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SignalDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"


class NewsArticle(BaseModel):
    """A single news article from any source."""
    source: str
    headline: str
    summary: str = ""
    url: str = ""
    tickers: list[str] = Field(default_factory=list)
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_sentiment_score: float | None = None  # pre-scored from source (-1 to 1)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def article_id(self) -> str:
        return f"{self.source}:{hash(self.headline)}"


class ScoredArticle(BaseModel):
    """An article after sentiment scoring."""
    article: NewsArticle
    sentiment: Sentiment
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    impact: Impact
    scoring_method: str  # "source_prescored", "llm_claude", etc.
    scored_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TradingSignal(BaseModel):
    """A buy/sell/neutral signal derived from scored articles."""
    ticker: str
    direction: SignalDirection
    strength: float = Field(ge=0.0, le=1.0)
    sentiment_score: float  # aggregate sentiment
    confidence: float
    source_count: int  # how many articles contributed
    headlines: list[str]  # contributing headlines
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
