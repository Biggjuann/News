"""
Signal aggregation engine.

Takes scored articles and produces trading signals by:
1. Grouping by ticker
2. Weighting by confidence, impact, and recency
3. Generating BUY/SELL/NEUTRAL signals when thresholds are crossed
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from config.settings import settings
from src.models import ScoredArticle, TradingSignal, SignalDirection, Impact

logger = logging.getLogger(__name__)

IMPACT_WEIGHT = {
    Impact.HIGH: 3.0,
    Impact.MEDIUM: 1.5,
    Impact.LOW: 1.0,
}


class SignalAggregator:
    def __init__(self):
        # Rolling window of scored articles per ticker
        self._article_buffer: dict[str, list[ScoredArticle]] = defaultdict(list)
        # Most recent signal per ticker
        self.active_signals: dict[str, TradingSignal] = {}

    def ingest(self, scored_articles: list[ScoredArticle]):
        """Add scored articles to the rolling buffer."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=settings.signal_expiry_seconds)

        for sa in scored_articles:
            for ticker in sa.article.tickers:
                self._article_buffer[ticker].append(sa)

        # Prune expired articles from all buffers
        for ticker in list(self._article_buffer.keys()):
            self._article_buffer[ticker] = [
                sa for sa in self._article_buffer[ticker]
                if sa.scored_at > cutoff
            ]
            if not self._article_buffer[ticker]:
                del self._article_buffer[ticker]

        # Prune expired signals
        for ticker in list(self.active_signals.keys()):
            sig = self.active_signals[ticker]
            if sig.expires_at and sig.expires_at < now:
                del self.active_signals[ticker]

    def generate_signals(self) -> list[TradingSignal]:
        """Generate signals from the current article buffer."""
        now = datetime.now(timezone.utc)
        new_signals = []

        for ticker, articles in self._article_buffer.items():
            if not articles:
                continue

            # Weighted sentiment calculation
            total_weight = 0.0
            weighted_score = 0.0
            headlines = []

            for sa in articles:
                # Weight = confidence × impact_weight × recency_decay
                age_seconds = (now - sa.scored_at).total_seconds()
                recency = max(0.1, 1.0 - (age_seconds / settings.signal_expiry_seconds))
                weight = sa.confidence * IMPACT_WEIGHT[sa.impact] * recency

                weighted_score += sa.sentiment_score * weight
                total_weight += weight
                headlines.append(sa.article.headline[:100])

            if total_weight == 0:
                continue

            avg_sentiment = weighted_score / total_weight
            avg_confidence = sum(sa.confidence for sa in articles) / len(articles)

            # Determine direction
            if avg_sentiment >= settings.buy_signal_threshold and avg_confidence >= settings.min_confidence:
                direction = SignalDirection.BUY
            elif avg_sentiment <= settings.sell_signal_threshold and avg_confidence >= settings.min_confidence:
                direction = SignalDirection.SELL
            else:
                direction = SignalDirection.NEUTRAL

            signal = TradingSignal(
                ticker=ticker,
                direction=direction,
                strength=min(abs(avg_sentiment), 1.0),
                sentiment_score=round(avg_sentiment, 4),
                confidence=round(avg_confidence, 4),
                source_count=len(articles),
                headlines=headlines[:5],  # top 5 headlines
                created_at=now,
                expires_at=now + timedelta(seconds=settings.signal_expiry_seconds),
            )

            # Only emit if direction changed or it's a new signal
            existing = self.active_signals.get(ticker)
            if existing is None or existing.direction != direction:
                if direction != SignalDirection.NEUTRAL:
                    logger.info(
                        f"SIGNAL: {direction.value} {ticker} | "
                        f"sentiment={avg_sentiment:.3f} confidence={avg_confidence:.3f} "
                        f"sources={len(articles)}"
                    )
                    new_signals.append(signal)

            self.active_signals[ticker] = signal

        return new_signals
