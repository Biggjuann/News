"""
Signal aggregation engine.

Takes scored articles and produces trading signals by:
1. Grouping by ticker
2. Requiring multiple sources to confirm a direction
3. Weighting by confidence, impact, and recency
4. Enforcing cooldowns to prevent signal flipping
5. Generating BUY/SELL signals only when conviction is strong
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from config.settings import settings
from src.models import ScoredArticle, TradingSignal, SignalDirection, Impact, Sentiment

logger = logging.getLogger(__name__)

IMPACT_WEIGHT = {
    Impact.HIGH: 3.0,
    Impact.MEDIUM: 1.5,
    Impact.LOW: 1.0,
}

# Trusted source prefixes that can trigger single-source signals.
# Trump's direct posts ARE the signal — don't require corroboration.
TRUSTED_SINGLE_SOURCE_PREFIXES = (
    "trump_truth_social",
    "x_realDonaldTrump",
    "x_POTUS",
    "x_WhiteHouse",
    "wh_briefing",
    "wh_presidential",
    "wh_statements",
)


def _is_trusted_source(source_name: str) -> bool:
    """Check if this source is trusted enough for single-source signals."""
    return any(source_name.startswith(p) for p in TRUSTED_SINGLE_SOURCE_PREFIXES)


class SignalAggregator:
    def __init__(self):
        # Rolling window of scored articles per ticker
        self._article_buffer: dict[str, list[ScoredArticle]] = defaultdict(list)
        # Most recent signal per ticker
        self.active_signals: dict[str, TradingSignal] = {}
        # Cooldown tracking: ticker → last signal time
        self._last_signal_time: dict[str, datetime] = {}
        # Track last signal direction for flip detection
        self._last_direction: dict[str, SignalDirection] = {}

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

            # --- FILTER 1: Minimum source count ---
            # Exception: trusted political sources (Trump posts, WH statements)
            # can trigger signals alone IF they have high confidence + impact.
            has_trusted_solo = any(
                _is_trusted_source(sa.article.source)
                and sa.confidence >= settings.trusted_solo_min_confidence
                and sa.impact == Impact.HIGH
                for sa in articles
            )

            if len(articles) < settings.min_sources and not has_trusted_solo:
                continue

            # --- Calculate weighted sentiment ---
            total_weight = 0.0
            weighted_score = 0.0
            headlines = []
            bullish_count = 0
            bearish_count = 0

            for sa in articles:
                age_seconds = (now - sa.scored_at).total_seconds()
                recency = max(0.1, 1.0 - (age_seconds / settings.signal_expiry_seconds))
                weight = sa.confidence * IMPACT_WEIGHT[sa.impact] * recency

                weighted_score += sa.sentiment_score * weight
                total_weight += weight
                headlines.append(sa.article.headline[:100])

                if sa.sentiment == Sentiment.BULLISH:
                    bullish_count += 1
                elif sa.sentiment == Sentiment.BEARISH:
                    bearish_count += 1

            if total_weight == 0:
                continue

            avg_sentiment = weighted_score / total_weight
            avg_confidence = sum(sa.confidence for sa in articles) / len(articles)

            # --- FILTER 2: Sentiment agreement ---
            # Require a supermajority of articles to agree on direction
            total_directional = bullish_count + bearish_count
            if total_directional > 0:
                if avg_sentiment > 0:
                    agreement = bullish_count / total_directional
                else:
                    agreement = bearish_count / total_directional
            else:
                agreement = 0.0

            # Skip agreement check for trusted solo signals
            if agreement < settings.min_agreement and not has_trusted_solo:
                continue

            # --- Determine direction ---
            if avg_sentiment >= settings.buy_signal_threshold and avg_confidence >= settings.min_confidence:
                direction = SignalDirection.BUY
            elif avg_sentiment <= settings.sell_signal_threshold and avg_confidence >= settings.min_confidence:
                direction = SignalDirection.SELL
            else:
                direction = SignalDirection.NEUTRAL

            if direction == SignalDirection.NEUTRAL:
                self.active_signals[ticker] = TradingSignal(
                    ticker=ticker,
                    direction=direction,
                    strength=min(abs(avg_sentiment), 1.0),
                    sentiment_score=round(avg_sentiment, 4),
                    confidence=round(avg_confidence, 4),
                    source_count=len(articles),
                    headlines=headlines[:5],
                    created_at=now,
                    expires_at=now + timedelta(seconds=settings.signal_expiry_seconds),
                )
                continue

            # --- FILTER 3: Cooldown ---
            last_time = self._last_signal_time.get(ticker)
            if last_time:
                elapsed = (now - last_time).total_seconds()
                last_dir = self._last_direction.get(ticker)

                # If flipping direction (BUY→SELL or SELL→BUY), require longer cooldown
                if last_dir and last_dir != direction:
                    if elapsed < settings.signal_flip_cooldown_seconds:
                        logger.debug(
                            f"Suppressed {direction.value} {ticker}: "
                            f"flip cooldown ({elapsed:.0f}s < {settings.signal_flip_cooldown_seconds}s)"
                        )
                        continue
                else:
                    # Same direction repeat — shorter cooldown
                    if elapsed < settings.signal_cooldown_seconds:
                        continue

            signal = TradingSignal(
                ticker=ticker,
                direction=direction,
                strength=min(abs(avg_sentiment), 1.0),
                sentiment_score=round(avg_sentiment, 4),
                confidence=round(avg_confidence, 4),
                source_count=len(articles),
                headlines=headlines[:5],
                created_at=now,
                expires_at=now + timedelta(seconds=settings.signal_expiry_seconds),
            )

            # Only emit if direction changed or it's a new signal
            existing = self.active_signals.get(ticker)
            if existing is None or existing.direction != direction:
                logger.info(
                    f"SIGNAL: {direction.value} {ticker} | "
                    f"sentiment={avg_sentiment:.3f} confidence={avg_confidence:.3f} "
                    f"sources={len(articles)} agreement={agreement:.0%}"
                )
                new_signals.append(signal)
                self._last_signal_time[ticker] = now
                self._last_direction[ticker] = direction

            self.active_signals[ticker] = signal

        return new_signals
