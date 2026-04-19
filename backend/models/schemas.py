"""Pydantic v2 schemas — request/response contracts for the FastAPI layer."""

from datetime import datetime
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


# ── Ticker ─────────────────────────────────────────────────────────────────────

class TickerBase(BaseModel):
    symbol: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap: Optional[int] = None
    avg_volume: Optional[int] = None
    exchange: Optional[str] = None


class TickerResponse(TickerBase):
    model_config = ConfigDict(from_attributes=True)
    is_active: bool
    last_updated: Optional[datetime] = None


# ── Price ──────────────────────────────────────────────────────────────────────

class PriceBarResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    symbol: str
    ts: datetime
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None


# ── Signal ─────────────────────────────────────────────────────────────────────

class TradeLevels(BaseModel):
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    risk_reward: Optional[float] = None


class TechnicalSnapshot(BaseModel):
    price: Optional[float] = None
    rsi_14: Optional[float] = None
    bb_pct: Optional[float] = None
    ma20: Optional[float] = None
    ma50: Optional[float] = None
    ma200: Optional[float] = None
    atr: Optional[float] = None
    zscore: Optional[float] = None


class NewsItem(BaseModel):
    title: str
    url: Optional[str] = None
    source: Optional[str] = None
    published_at: Optional[datetime] = None
    sentiment: Optional[str] = None


class SignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    symbol: str
    ts: datetime

    # Scores
    dip_score: Optional[float] = Field(None, ge=0, le=100)
    sentiment_score: Optional[float] = Field(None, ge=0, le=100)
    composite_score: Optional[float] = Field(None, ge=0, le=100)

    # Decision
    bias: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0, le=100)
    reasoning: Optional[str] = None

    # Sub-scores
    rsi_score: Optional[float] = None
    bb_score: Optional[float] = None
    ma_deviation_score: Optional[float] = None
    volume_score: Optional[float] = None
    zscore_score: Optional[float] = None
    mean_rev_score: Optional[float] = None
    support_score: Optional[float] = None

    # Context
    sentiment_label: Optional[str] = None
    news_type: Optional[str] = None
    spy_trend: Optional[str] = None
    sector_rs: Optional[float] = None
    vix_level: Optional[float] = None

    # Trade levels + technicals (nested)
    levels: Optional[TradeLevels] = None
    technicals: Optional[TechnicalSnapshot] = None
    news: Optional[List[NewsItem]] = None

    # Ticker metadata (joined)
    name: Optional[str] = None
    sector: Optional[str] = None


class SignalListResponse(BaseModel):
    signals: List[SignalResponse]
    total: int
    page: int
    page_size: int


# ── Watchlist ──────────────────────────────────────────────────────────────────

class WatchlistAdd(BaseModel):
    symbol: str
    name: str = "Default"
    notes: Optional[str] = None


class WatchlistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    symbol: str
    added_at: datetime
    notes: Optional[str] = None


# ── Backtest ───────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    start_date: datetime
    end_date: datetime
    universe: str = "SP500"            # "SP500" | "NASDAQ100" | list of tickers
    dip_score_threshold: float = 60.0
    sentiment_score_threshold: float = 40.0
    hold_days: int = 5
    stop_loss_atr_mult: float = 2.0
    target_atr_mult: float = 4.0
    min_confidence: float = 60.0


class BacktestTradeRecord(BaseModel):
    symbol: str
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    return_pct: float
    exit_reason: str    # "target_1" | "stop_loss" | "time_exit"


class BacktestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    run_at: datetime
    total_trades: int
    win_rate: float
    avg_return: float
    max_drawdown: float
    profit_factor: float
    sharpe_ratio: float
    equity_curve: list
    trade_log: list


# ── WebSocket messages ─────────────────────────────────────────────────────────

class WSMessage(BaseModel):
    type: str           # "signal_update" | "scan_complete" | "price_tick"
    payload: dict
    ts: datetime = Field(default_factory=datetime.utcnow)
