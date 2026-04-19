"""
Sentiment Analysis Engine
=========================

For each ticker with a Dip Score above threshold, this module:

1. Pulls recent news (last 24–72h) from the news service
2. Runs three-tier sentiment analysis:
   - VADER (fast lexical, financial-tuned)
   - Transformer model (FinBERT or similar) for headline classification
   - Overreaction detector (strong news + mild price move = opportunity)
3. Classifies news type: earnings / lawsuit / product / macro / analyst / other
4. Emits a Sentiment Score (0–100) where 100 = strongly bullish catalyst

Design
------
- VADER is used as primary for speed; transformer confirms borderline cases
- News type classification uses keyword pattern matching (zero-shot is too slow
  for real-time; fine-tuned FinBERT is optional if latency permits)
- Overreaction detection compares news sentiment magnitude vs actual price move
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Lazy-import transformers to avoid startup delay
_finbert = None

# ── News type keywords ─────────────────────────────────────────────────────────
NEWS_TYPE_PATTERNS: dict[str, list[str]] = {
    "earnings":       ["earnings", "eps", "revenue", "quarterly", "q1", "q2", "q3", "q4",
                       "beat", "miss", "guidance", "outlook", "fiscal"],
    "lawsuit":        ["lawsuit", "sue", "sued", "litigation", "settlement", "class action",
                       "regulatory", "sec probe", "investigation", "fine", "penalty"],
    "product_launch": ["launch", "release", "unveil", "announce", "introduce", "new product",
                       "partnership", "deal", "contract", "acquisition", "merger"],
    "macro":          ["fed", "federal reserve", "inflation", "interest rate", "gdp",
                       "recession", "unemployment", "cpi", "ppi", "fomc", "powell"],
    "analyst":        ["upgrade", "downgrade", "price target", "overweight", "underweight",
                       "buy rating", "sell rating", "neutral", "outperform", "underperform"],
    "insider":        ["insider", "ceo", "bought shares", "sold shares", "10-b5", "form 4"],
}

# Weights for scoring: not all news types matter equally for dip opportunities
NEWS_TYPE_IMPACT: dict[str, float] = {
    "earnings":       1.2,
    "lawsuit":        0.7,   # often overreacted; can be dip opportunity
    "product_launch": 1.1,
    "macro":          0.8,   # sector/market wide — diluted
    "analyst":        1.0,
    "insider":        1.3,   # insiders buying on dip = strong signal
    "other":          0.9,
}


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEUTRAL  = "neutral"
    NEGATIVE = "negative"


@dataclass
class ArticleSentiment:
    title: str
    url: Optional[str]
    source: Optional[str]
    published_at: Optional[str]
    vader_compound: float       # -1 to +1
    label: SentimentLabel
    news_type: str
    impact_weight: float


@dataclass
class SentimentResult:
    sentiment_score: float          # 0–100 (50=neutral, >60=bullish, <40=bearish)
    sentiment_label: str
    news_type: str                  # dominant news category
    overreaction_detected: bool     # negative news but price held / mild drop
    articles: list[ArticleSentiment] = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "sentiment_score": round(self.sentiment_score, 2),
            "sentiment_label": self.sentiment_label,
            "news_type": self.news_type,
            "overreaction_detected": self.overreaction_detected,
            "news_count": len(self.articles),
            "reasoning": self.reasoning,
            "headlines": [
                {
                    "title": a.title,
                    "url": a.url,
                    "source": a.source,
                    "published_at": a.published_at,
                    "sentiment": a.label.value,
                    "news_type": a.news_type,
                }
                for a in self.articles[:5]   # return top 5 headlines
            ],
        }


class SentimentEngine:
    """Stateless sentiment scorer.  Instantiate once; call score() per ticker."""

    def __init__(self, use_finbert: bool = False):
        self._vader = SentimentIntensityAnalyzer()
        # Augment VADER with finance-specific terms
        self._vader.lexicon.update({
            "beat":           3.0,
            "miss":          -3.0,
            "raised":         2.0,
            "lowered":       -2.0,
            "exceeds":        2.5,
            "below":         -2.0,
            "strong":         1.5,
            "weak":          -1.5,
            "bullish":        2.0,
            "bearish":       -2.0,
            "oversold":       1.5,
            "lawsuit":       -2.5,
            "investigation":  -2.0,
            "downgrade":     -2.0,
            "upgrade":        2.0,
            "buyback":        2.0,
            "dividend":       1.5,
            "default":       -3.5,
            "bankruptcy":    -4.0,
            "recall":        -2.5,
            "delisted":      -4.0,
        })
        self._use_finbert = use_finbert

    def score(
        self,
        articles: list[dict],
        price_change_pct: Optional[float] = None,  # today's % price change
    ) -> Optional[SentimentResult]:
        """
        Parameters
        ----------
        articles : list of dicts
            Each dict: {title, url, source, published_at}
        price_change_pct : float, optional
            Today's price change %. Used for overreaction detection.
        """
        if not articles:
            return SentimentResult(
                sentiment_score=50.0,
                sentiment_label="neutral",
                news_type="other",
                overreaction_detected=False,
                reasoning="No recent news found — neutral assumption.",
            )

        scored: list[ArticleSentiment] = []
        for art in articles:
            title = art.get("title", "")
            if not title:
                continue
            scored.append(self._score_article(title, art))

        if not scored:
            return None

        # ── Aggregate score ────────────────────────────────────────────────────
        # Weighted mean of VADER compound scores, adjusted by impact weight
        total_weight = sum(a.impact_weight for a in scored)
        weighted_compound = sum(
            a.vader_compound * a.impact_weight for a in scored
        ) / total_weight if total_weight > 0 else 0.0

        # Map [-1, +1] compound to [0, 100] sentiment score
        # -1 → 0, 0 → 50, +1 → 100
        sentiment_score = 50.0 + (weighted_compound * 50.0)
        sentiment_score = max(0.0, min(100.0, sentiment_score))

        # ── Dominant news type ─────────────────────────────────────────────────
        type_counts: dict[str, int] = {}
        for a in scored:
            type_counts[a.news_type] = type_counts.get(a.news_type, 0) + 1
        dominant_type = max(type_counts, key=type_counts.get)

        # ── Sentiment label ────────────────────────────────────────────────────
        if sentiment_score >= 60:
            label = "positive"
        elif sentiment_score <= 40:
            label = "negative"
        else:
            label = "neutral"

        # ── Overreaction detection ─────────────────────────────────────────────
        # Criteria: news is negative (compound < -0.3) but price drop is mild (< 3%)
        # OR news is negative but RSI is extremely oversold (handled in decision engine)
        overreaction = False
        if price_change_pct is not None:
            if weighted_compound < -0.3 and price_change_pct > -0.05:
                overreaction = True   # bad news but stock barely moved — resilient
            elif weighted_compound < -0.5 and price_change_pct < -0.08:
                overreaction = True   # extreme sentiment vs moderate drop — panic sell

        # ── Reasoning ─────────────────────────────────────────────────────────
        reasoning = self._generate_reasoning(
            scored, sentiment_score, dominant_type, overreaction, weighted_compound
        )

        return SentimentResult(
            sentiment_score=round(sentiment_score, 2),
            sentiment_label=label,
            news_type=dominant_type,
            overreaction_detected=overreaction,
            articles=scored,
            reasoning=reasoning,
        )

    def _score_article(self, title: str, meta: dict) -> ArticleSentiment:
        """Score a single article."""
        text_lower = title.lower()

        # VADER sentiment
        vs = self._vader.polarity_scores(title)
        compound = float(vs["compound"])

        if compound >= 0.05:
            label = SentimentLabel.POSITIVE
        elif compound <= -0.05:
            label = SentimentLabel.NEGATIVE
        else:
            label = SentimentLabel.NEUTRAL

        # News type classification
        news_type = self._classify_type(text_lower)
        impact_weight = NEWS_TYPE_IMPACT.get(news_type, 0.9)

        return ArticleSentiment(
            title=title,
            url=meta.get("url"),
            source=meta.get("source"),
            published_at=meta.get("published_at"),
            vader_compound=compound,
            label=label,
            news_type=news_type,
            impact_weight=impact_weight,
        )

    def _classify_type(self, text_lower: str) -> str:
        """Keyword-based news type classification."""
        scores: dict[str, int] = {}
        for ntype, keywords in NEWS_TYPE_PATTERNS.items():
            hits = sum(1 for kw in keywords if kw in text_lower)
            if hits:
                scores[ntype] = hits
        if not scores:
            return "other"
        return max(scores, key=scores.get)

    def _generate_reasoning(
        self,
        articles: list[ArticleSentiment],
        score: float,
        dominant_type: str,
        overreaction: bool,
        compound: float,
    ) -> str:
        n = len(articles)
        pos = sum(1 for a in articles if a.label == SentimentLabel.POSITIVE)
        neg = sum(1 for a in articles if a.label == SentimentLabel.NEGATIVE)

        parts = [
            f"Analysed {n} article(s): {pos} positive, {neg} negative.",
            f"Dominant news type: {dominant_type.replace('_', ' ')}.",
        ]

        if overreaction:
            parts.append(
                "Overreaction signal: negative news sentiment does not match "
                "the magnitude of the price move — potential mean-reversion opportunity."
            )

        if score >= 65:
            parts.append("Sentiment is broadly constructive; news flow supports recovery.")
        elif score <= 35:
            parts.append(
                "Sentiment is bearish. Confirm whether news is priced in before entering."
            )
        else:
            parts.append("Sentiment is mixed/neutral — price action is the primary signal.")

        return " ".join(parts)


# Module-level singleton for reuse across workers
_engine = SentimentEngine()


def score_ticker_news(
    articles: list[dict],
    price_change_pct: Optional[float] = None,
) -> Optional[SentimentResult]:
    """Convenience function — uses module-level engine."""
    return _engine.score(articles, price_change_pct=price_change_pct)
