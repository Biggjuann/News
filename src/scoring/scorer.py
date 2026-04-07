"""
Two-layer sentiment scoring engine.

Layer 1: Use pre-scored sentiment from sources that provide it (Alpha Vantage, FMP).
Layer 2: For unscored headlines (Finnhub, RSS), use Claude Haiku for fast classification.
"""

import json
import logging
from datetime import datetime, timezone

import anthropic

from config.settings import settings
from src.models import NewsArticle, ScoredArticle, Sentiment, Impact

logger = logging.getLogger(__name__)

LLM_PROMPT = """You are a financial news sentiment classifier. Analyze the following headline and summary for market sentiment.

Headline: {headline}
Summary: {summary}
Related tickers: {tickers}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "score": <float from -1.0 to 1.0>,
  "confidence": <float from 0.0 to 1.0>,
  "impact": "high" | "medium" | "low"
}}

Rules:
- score: -1.0 = extremely bearish, 0.0 = neutral, 1.0 = extremely bullish
- confidence: how confident you are in the classification
- impact: "high" for earnings, Fed decisions, major geopolitical events; "medium" for analyst upgrades/downgrades, sector moves; "low" for routine news
- Be decisive. Most financial news is NOT neutral — lean into the direction."""


class SentimentScorer:
    def __init__(self):
        self._client: anthropic.AsyncAnthropic | None = None
        if settings.anthropic_api_key:
            self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def score(self, article: NewsArticle) -> ScoredArticle:
        """Score an article using pre-scored data or LLM fallback."""
        if article.raw_sentiment_score is not None:
            return self._from_prescored(article)
        if self._client:
            return await self._score_with_llm(article)
        return self._default_neutral(article)

    async def score_batch(self, articles: list[NewsArticle]) -> list[ScoredArticle]:
        """Score a batch of articles."""
        results = []
        for article in articles:
            try:
                scored = await self.score(article)
                results.append(scored)
            except Exception as e:
                logger.error(f"Scoring error for '{article.headline[:50]}': {e}")
                results.append(self._default_neutral(article))
        return results

    def _from_prescored(self, article: NewsArticle) -> ScoredArticle:
        """Convert a pre-scored raw sentiment into a ScoredArticle."""
        score = article.raw_sentiment_score
        if score > 0.15:
            sentiment = Sentiment.BULLISH
        elif score < -0.15:
            sentiment = Sentiment.BEARISH
        else:
            sentiment = Sentiment.NEUTRAL

        abs_score = abs(score)
        if abs_score > 0.6:
            impact = Impact.HIGH
        elif abs_score > 0.3:
            impact = Impact.MEDIUM
        else:
            impact = Impact.LOW

        return ScoredArticle(
            article=article,
            sentiment=sentiment,
            sentiment_score=score,
            confidence=min(abs_score + 0.3, 1.0),  # pre-scored sources get a confidence boost
            impact=impact,
            scoring_method="source_prescored",
        )

    async def _score_with_llm(self, article: NewsArticle) -> ScoredArticle:
        """Use Claude Haiku to classify sentiment."""
        prompt = LLM_PROMPT.format(
            headline=article.headline,
            summary=article.summary[:300],
            tickers=", ".join(article.tickers) if article.tickers else "unknown",
        )

        try:
            response = await self._client.messages.create(
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(text)

            sentiment = Sentiment(data["sentiment"])
            score = max(-1.0, min(1.0, float(data["score"])))
            confidence = max(0.0, min(1.0, float(data["confidence"])))
            impact = Impact(data["impact"])

            return ScoredArticle(
                article=article,
                sentiment=sentiment,
                sentiment_score=score,
                confidence=confidence,
                impact=impact,
                scoring_method="llm_claude",
            )
        except Exception as e:
            logger.error(f"LLM scoring failed: {e}")
            return self._default_neutral(article)

    def _default_neutral(self, article: NewsArticle) -> ScoredArticle:
        """Fallback when no scoring method is available."""
        return ScoredArticle(
            article=article,
            sentiment=Sentiment.NEUTRAL,
            sentiment_score=0.0,
            confidence=0.1,
            impact=Impact.LOW,
            scoring_method="default_fallback",
        )
