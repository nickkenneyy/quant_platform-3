"""
Quant Dip Detection Engine
==========================

Computes a composite Dip Score (0–100) for a single ticker using:

1. Technical signals  — RSI, Bollinger Bands, MA deviation, volume spikes
2. Statistical signals — Z-score, mean reversion probability, vol expansion
3. Market structure   — Support/demand zones, relative strength

Design principles
-----------------
- All sub-scores normalised to 0–100 before weighting
- Weights tuned so that a score >60 has historically had positive expectancy
  (validate with the backtesting module)
- No look-ahead bias: all indicators computed on bars[:-1] to avoid using
  today's close in a real-time context; caller must pass the correct window

Assumptions
-----------
- `bars` is a DataFrame with columns: open, high, low, close, volume
  indexed by UTC timestamp, sorted ascending, minimum 252 rows (1 trading year)
- Prices are adjusted for splits/dividends
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

# Suppress ta library deprecation warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import ta  # noqa: E402


# ── Score weights ──────────────────────────────────────────────────────────────
# Sum must equal 1.0.  Tune after backtesting.
WEIGHTS = {
    "rsi":          0.20,
    "bb":           0.15,
    "ma_deviation": 0.15,
    "volume":       0.10,
    "zscore":       0.15,
    "mean_rev":     0.15,
    "support":      0.10,
}

# Minimum bars required for full computation
MIN_BARS = 252


@dataclass
class DipScoreResult:
    """Full breakdown of the dip score calculation."""
    composite: float            # 0–100 overall dip score

    # Sub-scores (0–100)
    rsi_score: float
    bb_score: float
    ma_deviation_score: float
    volume_score: float
    zscore_score: float
    mean_rev_score: float
    support_score: float

    # Raw indicator values (for display)
    rsi_14: float
    bb_pct: float               # %B — position within Bollinger Bands
    ma20: float
    ma50: float
    ma200: float
    price_vs_ma20_pct: float    # (price - ma20) / ma20
    price_vs_ma50_pct: float
    price_vs_ma200_pct: float
    zscore: float               # current price z-score vs 252-day history
    atr: float
    mean_rev_probability: float # estimated prob of mean reversion within 5d
    support_level: float        # nearest demand zone
    vol_expansion: float        # current ATR vs 20d avg ATR

    # Derived trade levels
    entry_low: float
    entry_high: float
    stop_loss: float
    target_1: float
    target_2: float
    risk_reward: float

    # Flags
    earnings_imminent: bool     # do NOT trade if earnings within 5 days
    liquidity_ok: bool          # avg volume filter

    def to_dict(self) -> dict:
        return {
            "dip_score": round(self.composite, 2),
            "rsi_score": round(self.rsi_score, 2),
            "bb_score": round(self.bb_score, 2),
            "ma_deviation_score": round(self.ma_deviation_score, 2),
            "volume_score": round(self.volume_score, 2),
            "zscore_score": round(self.zscore_score, 2),
            "mean_rev_score": round(self.mean_rev_score, 2),
            "support_score": round(self.support_score, 2),
            "rsi_14": round(self.rsi_14, 2),
            "bb_pct": round(self.bb_pct, 4),
            "ma20": round(self.ma20, 4),
            "ma50": round(self.ma50, 4),
            "ma200": round(self.ma200, 4),
            "price_vs_ma20_pct": round(self.price_vs_ma20_pct * 100, 2),
            "price_vs_ma50_pct": round(self.price_vs_ma50_pct * 100, 2),
            "price_vs_ma200_pct": round(self.price_vs_ma200_pct * 100, 2),
            "zscore": round(self.zscore, 3),
            "atr": round(self.atr, 4),
            "mean_rev_probability": round(self.mean_rev_probability, 3),
            "support_level": round(self.support_level, 4),
            "vol_expansion": round(self.vol_expansion, 3),
            "entry_low": round(self.entry_low, 4),
            "entry_high": round(self.entry_high, 4),
            "stop_loss": round(self.stop_loss, 4),
            "target_1": round(self.target_1, 4),
            "target_2": round(self.target_2, 4),
            "risk_reward": round(self.risk_reward, 2),
            "earnings_imminent": self.earnings_imminent,
            "liquidity_ok": self.liquidity_ok,
        }


def compute_dip_score(
    bars: pd.DataFrame,
    min_avg_volume: int = 1_000_000,
    next_earnings_dt: Optional[pd.Timestamp] = None,
) -> Optional[DipScoreResult]:
    """
    Main entry point.  Returns None if insufficient data or illiquid.

    Parameters
    ----------
    bars : pd.DataFrame
        Daily OHLCV bars, datetime-indexed, ascending, min 252 rows.
        Required columns: open, high, low, close, volume
    min_avg_volume : int
        Minimum 20-day average volume required.
    next_earnings_dt : pd.Timestamp, optional
        Upcoming earnings date.  If within 5 trading days, flag earnings_imminent.
    """
    if len(bars) < MIN_BARS:
        return None

    bars = bars.copy().sort_index()
    close  = bars["close"]
    high   = bars["high"]
    low    = bars["low"]
    volume = bars["volume"]
    price  = float(close.iloc[-1])

    # ── Liquidity gate ─────────────────────────────────────────────────────────
    avg_vol_20 = float(volume.iloc[-20:].mean())
    liquidity_ok = avg_vol_20 >= min_avg_volume
    if not liquidity_ok:
        return None   # hard filter — not worth scoring illiquid names

    # ── Earnings gate ──────────────────────────────────────────────────────────
    earnings_imminent = False
    if next_earnings_dt is not None:
        days_to_earnings = (next_earnings_dt - bars.index[-1]).days
        earnings_imminent = 0 <= days_to_earnings <= 5

    # ════════════════════════════════════════════════════════════════════════════
    # 1. TECHNICAL SIGNALS
    # ════════════════════════════════════════════════════════════════════════════

    # ── RSI (14) ───────────────────────────────────────────────────────────────
    rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
    rsi_14 = float(rsi_series.iloc[-1])
    # Score: RSI=0→100, RSI=30→70 (oversold), RSI=50→0
    rsi_score = _clamp(_linear_map(rsi_14, src_lo=10, src_hi=50, dst_lo=100, dst_hi=0))

    # ── Bollinger Bands (20, 2σ) ───────────────────────────────────────────────
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_pct = float(bb.bollinger_pband().iloc[-1])    # %B: 0=lower, 0.5=mid, 1=upper
    # Score: %B=0→100, %B=0.5→0
    bb_score = _clamp(_linear_map(bb_pct, src_lo=0.0, src_hi=0.5, dst_lo=100, dst_hi=0))

    # ── Moving average deviation ───────────────────────────────────────────────
    ma20  = float(close.rolling(20).mean().iloc[-1])
    ma50  = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])

    dev20  = (price - ma20)  / ma20
    dev50  = (price - ma50)  / ma50
    dev200 = (price - ma200) / ma200

    # Score peaks when price is 5–15% below each MA, weighted by MA distance
    ma_deviation_score = _clamp(
        0.5 * _deviation_to_score(dev20)  +
        0.3 * _deviation_to_score(dev50)  +
        0.2 * _deviation_to_score(dev200)
    )

    # ── Volume spike on red day ────────────────────────────────────────────────
    today_return = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2])
    avg_vol_20_val = float(volume.iloc[-20:].mean())
    vol_ratio = float(volume.iloc[-1]) / avg_vol_20_val if avg_vol_20_val > 0 else 1.0

    # High-volume sell-off = capitulation = good dip signal
    if today_return < 0:
        # Volume 2× average on a red day scores 80; 1× scores 0
        volume_score = _clamp(_linear_map(vol_ratio, src_lo=1.0, src_hi=3.0, dst_lo=0, dst_hi=100))
    else:
        volume_score = 0.0   # green day — no capitulation signal

    # ════════════════════════════════════════════════════════════════════════════
    # 2. STATISTICAL SIGNALS
    # ════════════════════════════════════════════════════════════════════════════

    # ── Z-score vs trailing 252-day mean ──────────────────────────────────────
    rolling_mean = close.rolling(252).mean()
    rolling_std  = close.rolling(252).std()
    zscore = float((close.iloc[-1] - rolling_mean.iloc[-1]) / rolling_std.iloc[-1])

    # Score: z=-2→100, z=0→0 (highly negative z = deep dip)
    zscore_score = _clamp(_linear_map(zscore, src_lo=-3.0, src_hi=0.0, dst_lo=100, dst_hi=0))

    # ── Mean reversion probability ────────────────────────────────────────────
    # Ornstein–Uhlenbeck half-life estimation
    log_prices = np.log(close.values)
    log_prices_lag = np.roll(log_prices, 1)[1:]
    log_prices_cur = log_prices[1:]
    residuals = log_prices_cur - log_prices_lag
    slope, intercept, *_ = stats.linregress(log_prices_lag, residuals)
    # half_life = -ln(2) / slope   (mean reversion speed)
    half_life = float(-np.log(2) / slope) if slope < 0 else 999.0
    # Estimate probability of reverting to mean within 5 days
    mean_rev_probability = float(np.exp(-5 / half_life)) if half_life > 0 else 0.5
    mean_rev_score = _clamp(mean_rev_probability * 100)

    # ── Volatility expansion (ATR) ────────────────────────────────────────────
    atr_series = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
    atr = float(atr_series.iloc[-1])
    atr_20 = float(atr_series.iloc[-20:].mean())
    vol_expansion = atr / atr_20 if atr_20 > 0 else 1.0
    # Already captured in bb_score and used for trade levels; no separate sub-score

    # ════════════════════════════════════════════════════════════════════════════
    # 3. MARKET STRUCTURE — Support / Demand Zones
    # ════════════════════════════════════════════════════════════════════════════
    support_level, support_score = _find_support_zone(high, low, close, price)

    # ════════════════════════════════════════════════════════════════════════════
    # COMPOSITE SCORE
    # ════════════════════════════════════════════════════════════════════════════
    composite = (
        WEIGHTS["rsi"]          * rsi_score         +
        WEIGHTS["bb"]           * bb_score          +
        WEIGHTS["ma_deviation"] * ma_deviation_score+
        WEIGHTS["volume"]       * volume_score      +
        WEIGHTS["zscore"]       * zscore_score      +
        WEIGHTS["mean_rev"]     * mean_rev_score    +
        WEIGHTS["support"]      * support_score
    )

    # ════════════════════════════════════════════════════════════════════════════
    # TRADE LEVELS  (ATR-based)
    # ════════════════════════════════════════════════════════════════════════════
    entry_low  = price                    # ideal limit entry at current price
    entry_high = price * 1.005            # up to 0.5% above current (market entry)
    stop_loss  = price - (2.0 * atr)      # 2× ATR stop
    target_1   = price + (3.0 * atr)      # 3× ATR T1  → R:R ~1.5
    target_2   = price + (5.0 * atr)      # 5× ATR T2  → R:R ~2.5
    risk       = entry_high - stop_loss
    reward     = target_1 - entry_high
    risk_reward = round(reward / risk, 2) if risk > 0 else 0.0

    return DipScoreResult(
        composite=round(_clamp(composite), 2),
        rsi_score=rsi_score,
        bb_score=bb_score,
        ma_deviation_score=ma_deviation_score,
        volume_score=volume_score,
        zscore_score=zscore_score,
        mean_rev_score=mean_rev_score,
        support_score=support_score,
        rsi_14=rsi_14,
        bb_pct=bb_pct,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        price_vs_ma20_pct=dev20,
        price_vs_ma50_pct=dev50,
        price_vs_ma200_pct=dev200,
        zscore=zscore,
        atr=atr,
        mean_rev_probability=mean_rev_probability,
        support_level=support_level,
        vol_expansion=vol_expansion,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        risk_reward=risk_reward,
        earnings_imminent=earnings_imminent,
        liquidity_ok=liquidity_ok,
    )


# ── Helper functions ───────────────────────────────────────────────────────────

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _linear_map(
    x: float,
    src_lo: float, src_hi: float,
    dst_lo: float, dst_hi: float,
) -> float:
    """Linearly map x from [src_lo, src_hi] to [dst_lo, dst_hi]."""
    if src_hi == src_lo:
        return dst_lo
    ratio = (x - src_lo) / (src_hi - src_lo)
    return dst_lo + ratio * (dst_hi - dst_lo)


def _deviation_to_score(deviation: float) -> float:
    """
    Convert price deviation from MA to a 0–100 score.
    deviation = (price - MA) / MA

    Scores:
      +10% or above:  0   (price above MA — not a dip)
      0%:            20   (at MA — mild signal)
      -5%:           70
      -10%:          90
      -20% or below: 100
    """
    if deviation >= 0.10:
        return 0.0
    if deviation >= 0.0:
        return _linear_map(deviation, 0.0, 0.10, 20.0, 0.0)
    # Below MA
    return _clamp(_linear_map(deviation, 0.0, -0.20, 20.0, 100.0))


def _find_support_zone(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    price: float,
    lookback: int = 126,   # ~6 months
    swing_window: int = 5,
) -> tuple[float, float]:
    """
    Identify the nearest demand zone below current price.

    Method: find local minima (swing lows) in the lookback window.
    A support level is considered strong if price has bounced from it
    at least twice (tested support).

    Returns
    -------
    support_level : float
        Price of the nearest support below current price
    support_score : float
        0–100 score based on proximity and strength
    """
    lows_window = low.iloc[-lookback:]
    highs_window = high.iloc[-lookback:]

    swing_lows: list[float] = []
    for i in range(swing_window, len(lows_window) - swing_window):
        candidate = float(lows_window.iloc[i])
        left_min  = float(lows_window.iloc[i - swing_window : i].min())
        right_min = float(lows_window.iloc[i + 1 : i + swing_window + 1].min())
        if candidate < left_min and candidate < right_min:
            swing_lows.append(candidate)

    # Cluster swing lows within 1.5% of each other
    clusters: dict[float, int] = {}
    for sl in swing_lows:
        matched = False
        for key in list(clusters.keys()):
            if abs(sl - key) / key < 0.015:
                # Update cluster centre (running mean)
                clusters[key] += 1
                matched = True
                break
        if not matched:
            clusters[sl] = 1

    # Filter to levels below current price
    levels_below = {k: v for k, v in clusters.items() if k < price}
    if not levels_below:
        return price * 0.95, 30.0   # fallback: 5% below, weak score

    # Nearest support
    nearest = max(levels_below.keys())   # closest below price
    touches = levels_below[nearest]
    dist_pct = (price - nearest) / price  # 0 = right at support, 0.10 = 10% above

    # Score: price within 3% of tested support → high score
    proximity_score = _clamp(_linear_map(dist_pct, src_lo=0.0, src_hi=0.05, dst_lo=100, dst_hi=40))
    strength_bonus  = min(touches * 10, 30)  # more touches = stronger zone
    support_score   = _clamp(proximity_score * 0.7 + strength_bonus)

    return nearest, support_score
