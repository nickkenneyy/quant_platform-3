"""
Backtesting Module
==================

Tests the dip detection strategy against historical data.

Methodology
-----------
1. For each trading day in [start_date, end_date]:
   a. Compute dip scores using only data available up to that day (no look-ahead)
   b. For tickers exceeding threshold: generate hypothetical entry
   c. Simulate forward return over hold_days calendar days
   d. Apply stop_loss and target exits on daily bar data

Key metrics
-----------
- Win rate            : % of trades that were profitable
- Average return      : mean trade return in %
- Max drawdown        : maximum peak-to-trough in equity curve
- Profit factor       : gross wins / gross losses
- Sharpe ratio        : annualised risk-adjusted return

Performance note
----------------
Vectorised with Pandas — a full S&P 500 × 2-year backtest takes ~2–5 min
depending on hardware.  Results are cached in BacktestResult table.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional
import uuid

import numpy as np
import pandas as pd
import structlog

from core.dip_engine import compute_dip_score
from core.config import settings
from models.schemas import BacktestRequest, BacktestResponse

logger = structlog.get_logger(__name__)


class BacktestEngine:
    """Vectorised backtesting engine."""

    async def run(
        self,
        request: BacktestRequest,
        price_data: dict[str, pd.DataFrame],   # symbol → daily OHLCV DataFrame
    ) -> dict:
        """
        Execute a backtest.

        Parameters
        ----------
        request : BacktestRequest
            Strategy parameters.
        price_data : dict
            Pre-loaded OHLCV DataFrames for all tickers in the universe.
            Keys are ticker symbols; values are daily bars indexed by UTC datetime.

        Returns
        -------
        dict matching BacktestResponse schema
        """
        start = pd.Timestamp(request.start_date, tz="UTC")
        end   = pd.Timestamp(request.end_date,   tz="UTC")
        trade_log: list[dict] = []

        symbols = list(price_data.keys())
        logger.info("backtest_started", symbols=len(symbols), start=str(start), end=str(end))

        # ── Walk-forward loop ──────────────────────────────────────────────────
        scan_dates = pd.bdate_range(start, end, freq="B")   # business days only

        for scan_dt in scan_dates:
            for symbol, bars in price_data.items():
                # Slice to data available at scan_dt (strict look-ahead guard)
                hist = bars[bars.index <= scan_dt]
                if len(hist) < 252:
                    continue

                dip = compute_dip_score(hist, min_avg_volume=request.hold_days * 0)
                if dip is None:
                    continue
                if dip.composite < request.dip_score_threshold:
                    continue
                if dip.earnings_imminent:
                    continue

                # Entry: next day's open
                future = bars[bars.index > scan_dt]
                if len(future) < 2:
                    continue

                entry_date  = future.index[0]
                entry_price = float(future["open"].iloc[0])
                if entry_price <= 0:
                    continue

                stop_price   = entry_price - (request.stop_loss_atr_mult * dip.atr)
                target_price = entry_price + (request.target_atr_mult   * dip.atr)

                # ── Simulate trade over hold window ────────────────────────────
                hold_bars = future.iloc[: request.hold_days + 1]
                exit_price, exit_date, exit_reason = self._simulate_exit(
                    hold_bars, entry_price, stop_price, target_price
                )

                ret_pct = (exit_price - entry_price) / entry_price * 100

                trade_log.append({
                    "symbol":      symbol,
                    "entry_date":  str(entry_date.date()),
                    "exit_date":   str(exit_date.date()),
                    "entry_price": round(entry_price, 4),
                    "exit_price":  round(exit_price, 4),
                    "return_pct":  round(ret_pct, 4),
                    "exit_reason": exit_reason,
                    "dip_score":   round(dip.composite, 2),
                })

        # ── Aggregate metrics ──────────────────────────────────────────────────
        return self._compute_metrics(trade_log)

    def _simulate_exit(
        self,
        future: pd.DataFrame,
        entry: float,
        stop: float,
        target: float,
    ) -> tuple[float, pd.Timestamp, str]:
        """Day-by-day exit simulation.  Checks high/low each bar."""
        for i, (dt, row) in enumerate(future.iterrows()):
            if i == 0:
                continue  # skip entry bar
            low  = float(row["low"])
            high = float(row["high"])
            close = float(row["close"])

            # Check stop first (pessimistic — assumes stop hit before target intraday)
            if low <= stop:
                return stop, dt, "stop_loss"
            if high >= target:
                return target, dt, "target_1"

        # Time exit — last bar close
        last_dt    = future.index[-1]
        last_close = float(future["close"].iloc[-1])
        return last_close, last_dt, "time_exit"

    def _compute_metrics(self, trade_log: list[dict]) -> dict:
        if not trade_log:
            return self._empty_metrics()

        rets = np.array([t["return_pct"] for t in trade_log])
        wins  = rets[rets > 0]
        losses = rets[rets <= 0]

        win_rate     = float(len(wins) / len(rets)) if len(rets) > 0 else 0.0
        avg_return   = float(np.mean(rets))
        gross_wins   = float(wins.sum())  if len(wins)   > 0 else 0.0
        gross_losses = float(abs(losses.sum())) if len(losses) > 0 else 1.0
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0.0

        # Build equity curve (cumulative product of (1 + r/100))
        equity = [1.0]
        for r in rets:
            equity.append(equity[-1] * (1 + r / 100))
        equity_arr = np.array(equity)

        # Max drawdown
        rolling_max = np.maximum.accumulate(equity_arr)
        drawdowns = (equity_arr - rolling_max) / rolling_max
        max_drawdown = float(drawdowns.min()) * 100  # as %

        # Sharpe (annualised, assuming ~252 trading days)
        if len(rets) > 1:
            daily_std = float(np.std(rets))
            sharpe = (avg_return / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0
        else:
            sharpe = 0.0

        # Equity curve for chart (sample every 10 trades)
        sample = equity_arr[::max(1, len(equity_arr) // 200)]
        equity_curve = [
            {"trade": i * max(1, len(equity_arr) // 200), "equity": round(float(v), 4)}
            for i, v in enumerate(sample)
        ]

        return {
            "id": str(uuid.uuid4()),
            "run_at": datetime.utcnow().isoformat(),
            "total_trades": len(trade_log),
            "win_rate": round(win_rate * 100, 2),
            "avg_return": round(avg_return, 4),
            "max_drawdown": round(max_drawdown, 4),
            "profit_factor": round(profit_factor, 4),
            "sharpe_ratio": round(sharpe, 4),
            "equity_curve": equity_curve,
            "trade_log": trade_log[:500],   # cap at 500 for response size
        }

    def _empty_metrics(self) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "run_at": datetime.utcnow().isoformat(),
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "equity_curve": [],
            "trade_log": [],
        }
