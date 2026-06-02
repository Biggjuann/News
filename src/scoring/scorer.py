"""
Sentiment scoring engine for news → stock impact.

Handles two types of news:
1. Political/policy news → SPY impact
2. Jensen Huang / Nvidia ecosystem news → per-ticker impact
"""

import json
import logging
from datetime import datetime, timezone

import anthropic

from config.settings import settings
from src.models import NewsArticle, ScoredArticle, Sentiment, Impact

logger = logging.getLogger(__name__)

POLITICAL_PROMPT = """You are an expert at analyzing political statements and policy announcements for their impact on the S&P 500 (SPY).

Source: {source}
Headline: {headline}
Context: {summary}
Tickers: {tickers}

Analyze this for its likely IMMEDIATE impact on the tickers listed. Respond with ONLY a JSON object:
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "score": <float from -1.0 to 1.0>,
  "confidence": <float from 0.0 to 1.0>,
  "impact": "high" | "medium" | "low",
  "reasoning": "<one sentence why>"
}}

Scoring guide for political/policy news → SPY:
BULLISH: Tariff reductions, trade deals, deregulation, tax cuts, peace deals, rate cuts
BEARISH: New tariffs, trade war escalation, sanctions, shutdowns, military action, antitrust
NEUTRAL: Personal attacks, routine ceremonies, vague promises, cultural commentary

Set confidence LOW (0.2-0.4) if vague. HIGH (0.7-0.9) if specific policy actions.
Set impact "high" ONLY for concrete actions (tariffs, executive orders, deals)."""

JENSEN_PROMPT = """You are an expert at analyzing how Jensen Huang (Nvidia CEO) comments, partnerships, and announcements affect specific stocks.

Source: {source}
Headline: {headline}
Context: {summary}
Tickers mentioned: {tickers}

Analyze this for its likely impact on the mentioned tickers. Respond with ONLY a JSON object:
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "score": <float from -1.0 to 1.0>,
  "confidence": <float from 0.0 to 1.0>,
  "impact": "high" | "medium" | "low",
  "reasoning": "<one sentence why>"
}}

Scoring guide for Jensen/Nvidia news:
BULLISH (+0.3 to +1.0):
- Jensen praises a company, announces partnership/deal/collaboration
- Nvidia selects a company as key supplier/customer/platform partner
- Positive comments about a sector Nvidia is investing in
- New product launches that benefit ecosystem partners
- Jensen says demand is strong, supply can't keep up

BEARISH (-0.3 to -1.0):
- Jensen criticizes a competitor or shifts away from a partner
- Nvidia drops a supplier or moves to in-house solution
- Jensen warns about demand slowdown or oversupply
- Negative comments about a specific company's technology

NEUTRAL (-0.2 to +0.2):
- Generic keynote content without specific company mentions
- Routine product updates with no partner implications
- Vague AI hype without concrete business impact

Set confidence HIGH when Jensen specifically names a company with a concrete action (deal, partnership, order).
Set confidence LOW for generic commentary or when the connection to a ticker is indirect."""


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
            confidence=min(abs_score + 0.3, 1.0),
            impact=impact,
            scoring_method="source_prescored",
        )

    def _pick_prompt(self, article: NewsArticle) -> str:
        """Select the right prompt template based on article source."""
        is_jensen = "jensen" in article.source.lower() or "jensen" in article.headline.lower()
        template = JENSEN_PROMPT if is_jensen else POLITICAL_PROMPT
        return template.format(
            source=article.source,
            headline=article.headline,
            summary=article.summary[:300] if article.summary else "No additional context",
            tickers=", ".join(article.tickers) if article.tickers else "unknown",
        )

    async def _score_with_llm(self, article: NewsArticle) -> ScoredArticle:
        """Use Claude Haiku to classify sentiment."""
        prompt = self._pick_prompt(article)

        try:
            response = await self._client.messages.create(
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
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
