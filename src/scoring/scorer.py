"""
Sentiment scoring engine for political/policy news → SPY impact.

Uses Claude Haiku to classify Trump posts, White House statements,
and policy announcements for their market impact on SPY/S&P 500.
"""

import json
import logging
from datetime import datetime, timezone

import anthropic

from config.settings import settings
from src.models import NewsArticle, ScoredArticle, Sentiment, Impact

logger = logging.getLogger(__name__)

LLM_PROMPT = """You are an expert at analyzing political statements and policy announcements for their impact on the S&P 500 (SPY).

Source: {source}
Post/Statement: {headline}
Additional context: {summary}

Analyze this for its likely IMMEDIATE impact on SPY. Respond with ONLY a JSON object:
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "score": <float from -1.0 to 1.0>,
  "confidence": <float from 0.0 to 1.0>,
  "impact": "high" | "medium" | "low",
  "reasoning": "<one sentence why>"
}}

Scoring guide for political/policy news → SPY:
BULLISH (+0.3 to +1.0):
- Tariff reductions, trade deals, deregulation
- Tax cuts, pro-business executive orders
- Peace deals, ceasefire agreements
- Positive economic commentary ("economy is great", "markets will boom")
- Fed pressure for rate cuts

BEARISH (-0.3 to -1.0):
- New tariffs, trade war escalation, sanctions
- Government shutdown threats, debt ceiling issues
- Military action, geopolitical escalation
- Attacks on companies, sectors, or the Fed
- Regulatory crackdowns, antitrust threats

NEUTRAL (-0.2 to +0.2):
- Personal attacks on political opponents (no market impact)
- Routine ceremonial posts, holidays, rallies
- Restatements of known policy positions
- Social/cultural commentary with no economic angle

Set confidence LOW (0.2-0.4) if the post is vague or could be interpreted multiple ways.
Set confidence HIGH (0.7-0.9) if the post contains specific policy actions or clear economic implications.
Set impact to "high" ONLY for concrete policy actions (tariffs, executive orders, deals). Vague promises are "medium" at best."""


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
            source=article.source,
            headline=article.headline,
            summary=article.summary[:300] if article.summary else "No additional context",
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
