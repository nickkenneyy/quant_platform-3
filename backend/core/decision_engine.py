"""
Decision Engine
===============

Combines DipScoreResult + SentimentResult + market context into a
final trade recommendation with:
  - bias: BUY / WATCH / AVOID
  - confidence: 0–100%
  - reasoning: human-readable explanation

Scoring philosophy
------------------
The composite score is a weighted blend, with market regime as a multiplier.
In a strong bear market (SPY below 200 MA), all BUY signals become WATCH.
In a strong bull market, the threshold to upgrade WATCH → BUY is lower.

The confidence number represents the model's conviction given the available
evidence.  It is NOT a probability of profit — always remind users of this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.dip_engine import DipScoreResult
from core.sentiment_engine import SentimentResult


# ── Bias thresholds ────────────────────────────────────────────────────────────
BUY_COMPOSITE_THRESHOLD   = 62.0
WATCH_COMPOSITE_THRESHOLD = 45.0

# ── Component weights (must sum to 1.0) ────────────────────────────────────────
W_DIP_SCORE  = 0.55
W_SENTIMENT  = 0.25
W_MARKET_CTX = 0.20

# ── Market regime multipliers ──────────────────────────────────────────────────
REGIME_MULT = {
    "bullish": 1.10,   # boost composite by 10% in bull market
    "neutral": 1.00,
    "bearish": 0.80,   # reduce composite by 20% in bear market
}


@dataclass
class MarketContext:
    """Current macro/market state, computed separately."""
    spy_trend: str          # "bullish" | "neutral" | "bearish"
    spy_rsi: float
    spy_vs_ma200_pct: float  # (SPY - MA200) / MA200
    vix_level: float        # VIX index level
    sector_rs: float        # stock's relative strength vs sector (1.0 = equal)

    @property
    def regime(self) -> str:
        """Simplified market regime based on SPY position."""
        if self.spy_trend == "bullish" and self.spy_vs_ma200_pct > 0.02:
            return "bullish"
        if self.spy_trend == "bearish" and self.spy_vs_ma200_pct < -0.05:
            return "bearish"
        return "neutral"

    @property
    def vix_regime(self) -> str:
        if self.vix_level < 15:
            return "low"
        if self.vix_level < 25:
            return "normal"
        if self.vix_level < 35:
            return "elevated"
        return "fear"


@dataclass
class DecisionResult:
    bias: str               # BUY / WATCH / AVOID
    confidence: float       # 0–100
    composite_score: float  # 0–100
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "bias": self.bias,
            "confidence": round(self.confidence, 1),
            "composite_score": round(self.composite_score, 2),
            "reasoning": self.reasoning,
        }


def make_decision(
    dip: DipScoreResult,
    sentiment: Optional[SentimentResult],
    market: MarketContext,
) -> DecisionResult:
    """
    Combine all signals into a final trade decision.

    Override rules (applied after scoring):
    1. earnings_imminent → force AVOID regardless of score
    2. VIX > 35 AND bearish regime → cap at WATCH
    3. Sentiment is "negative" AND dip_score < 50 → force AVOID
    4. Sector RS < 0.85 (stock badly underperforming sector) → downgrade 10 pts
    """
    # ── Defaults if sentiment unavailable ────────────────────────────────────
    sent_score = sentiment.sentiment_score if sentiment else 50.0
    overreaction = sentiment.overreaction_detected if sentiment else False
    news_type = sentiment.news_type if sentiment else "unknown"

    # ── Market context score ───────────────────────────────────────────────────
    # Trend alignment: +bonus if stock direction agrees with market
    market_score = _compute_market_score(market)

    # ── Raw composite ──────────────────────────────────────────────────────────
    raw_composite = (
        W_DIP_SCORE  * dip.composite   +
        W_SENTIMENT  * sent_score      +
        W_MARKET_CTX * market_score
    )

    # Apply regime multiplier
    regime_mult = REGIME_MULT[market.regime]
    composite = min(100.0, raw_composite * regime_mult)

    # ── Sector underperformance penalty ───────────────────────────────────────
    if market.sector_rs < 0.85:
        composite -= 10.0
    elif market.sector_rs > 1.15:
        composite += 5.0    # relative strength bonus
    composite = max(0.0, min(100.0, composite))

    # ── Overreaction bonus ────────────────────────────────────────────────────
    if overreaction:
        composite = min(100.0, composite + 8.0)

    # ── Determine bias ─────────────────────────────────────────────────────────
    bias = _assign_bias(composite, dip, sentiment, market)

    # ── Confidence ────────────────────────────────────────────────────────────
    confidence = _compute_confidence(composite, dip, sentiment, market, bias)

    # ── Generate reasoning ────────────────────────────────────────────────────
    reasoning = _build_reasoning(
        bias, composite, confidence, dip, sentiment, market,
        news_type, overreaction
    )

    return DecisionResult(
        bias=bias,
        confidence=round(confidence, 1),
        composite_score=round(composite, 2),
        reasoning=reasoning,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _compute_market_score(m: MarketContext) -> float:
    """Convert market context to 0–100 score."""
    score = 50.0  # neutral base

    # SPY trend
    if m.spy_trend == "bullish":
        score += 20
    elif m.spy_trend == "bearish":
        score -= 25

    # VIX — high VIX means elevated fear but also potential for sharp reversals
    if m.vix_level > 35:
        score -= 10
    elif m.vix_level > 25:
        score -= 5
    elif m.vix_level < 15:
        score += 5

    return max(0.0, min(100.0, score))


def _assign_bias(
    composite: float,
    dip: DipScoreResult,
    sentiment: Optional[SentimentResult],
    market: MarketContext,
) -> str:
    """Apply override rules then threshold rules."""

    # Hard overrides ──────────────────────────────────────────────────────────
    if dip.earnings_imminent:
        return "AVOID"   # never trade into an earnings binary event

    sent_label = sentiment.sentiment_label if sentiment else "neutral"
    if sent_label == "negative" and dip.composite < 50:
        return "AVOID"   # bad news + weak technicals = stay out

    if market.vix_regime == "fear" and market.regime == "bearish":
        # Extreme fear in downtrend — cap everything at WATCH
        if composite >= BUY_COMPOSITE_THRESHOLD:
            return "WATCH"

    # Threshold rules ─────────────────────────────────────────────────────────
    if composite >= BUY_COMPOSITE_THRESHOLD:
        return "BUY"
    if composite >= WATCH_COMPOSITE_THRESHOLD:
        return "WATCH"
    return "AVOID"


def _compute_confidence(
    composite: float,
    dip: DipScoreResult,
    sentiment: Optional[SentimentResult],
    market: MarketContext,
    bias: str,
) -> float:
    """
    Confidence is highest when multiple independent signals agree.
    Penalised for conflicts (e.g. good technicals but bad sentiment).
    """
    base = composite  # start with composite as base

    # Signal agreement bonuses
    signals_agree = 0
    if dip.rsi_14 < 30:
        signals_agree += 1
    if dip.bb_pct < 0.2:
        signals_agree += 1
    if dip.zscore < -1.5:
        signals_agree += 1
    if sentiment and sentiment.sentiment_score > 55:
        signals_agree += 1
    if market.regime == "bullish":
        signals_agree += 1

    base += signals_agree * 2.5   # each agreeing signal adds 2.5% confidence

    # Conflict penalties
    sent_score = sentiment.sentiment_score if sentiment else 50.0
    if dip.composite > 60 and sent_score < 35:
        base -= 15   # great technicals but bad news
    if dip.earnings_imminent:
        base -= 30

    if bias == "AVOID":
        base = min(base, 40.0)   # cap AVOID signals at 40% confidence

    return max(0.0, min(100.0, base))


def _build_reasoning(
    bias: str,
    composite: float,
    confidence: float,
    dip: DipScoreResult,
    sentiment: Optional[SentimentResult],
    market: MarketContext,
    news_type: str,
    overreaction: bool,
) -> str:
    parts = []

    # Lead with bias
    parts.append(f"Signal: {bias} ({confidence:.0f}% confidence, composite {composite:.1f}/100).")

    # Technicals
    tech_notes = []
    if dip.rsi_14 < 30:
        tech_notes.append(f"RSI({dip.rsi_14:.1f}) is oversold")
    if dip.bb_pct < 0.2:
        tech_notes.append(f"%B({dip.bb_pct:.2f}) near lower Bollinger Band")
    if dip.zscore < -1.5:
        tech_notes.append(f"price z-score of {dip.zscore:.2f} indicates statistical undervaluation")
    if dip.price_vs_ma200_pct < -0.10:
        tech_notes.append(f"price is {abs(dip.price_vs_ma200_pct)*100:.1f}% below 200 MA")
    if dip.volume_score > 50:
        tech_notes.append("high-volume selling (potential capitulation)")
    if dip.support_score > 60:
        tech_notes.append(f"sitting near tested support at {dip.support_level:.2f}")
    if tech_notes:
        parts.append("Technical factors: " + "; ".join(tech_notes) + ".")

    # Sentiment
    if sentiment:
        sent_desc = sentiment.sentiment_label
        parts.append(
            f"News sentiment is {sent_desc} "
            f"(score {sentiment.sentiment_score:.0f}/100, "
            f"type: {news_type.replace('_', ' ')})."
        )
        if overreaction:
            parts.append(
                "Overreaction detected: news is more negative than the price "
                "move suggests — potential recovery opportunity."
            )

    # Market context
    parts.append(
        f"Market context: SPY is {market.spy_trend} "
        f"(VIX {market.vix_level:.1f}, "
        f"sector RS {market.sector_rs:.2f}x)."
    )

    # Risk note
    if dip.earnings_imminent:
        parts.append("WARNING: earnings within 5 days — binary risk event, signal invalidated.")
    if market.vix_level > 30:
        parts.append("Elevated volatility — reduce position size accordingly.")

    return " ".join(parts)
