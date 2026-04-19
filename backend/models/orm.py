"""
SQLAlchemy ORM models.

TimescaleDB hypertables are used for time-series data (price bars, signals).
Regular PG tables are used for reference data (tickers, watchlists, backtest results).
"""

from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import (
    Column, String, Float, Integer, BigInteger, Boolean,
    DateTime, Text, JSON, Enum, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid


class Base(DeclarativeBase):
    pass


class Bias(str, enum.Enum):
    BUY = "BUY"
    WATCH = "WATCH"
    AVOID = "AVOID"


class Ticker(Base):
    """Reference table for tradeable instruments."""
    __tablename__ = "tickers"

    symbol        = Column(String(10), primary_key=True)
    name          = Column(String(200))
    sector        = Column(String(100))
    industry      = Column(String(150))
    market_cap    = Column(BigInteger)
    avg_volume    = Column(BigInteger)
    exchange      = Column(String(20))
    is_active     = Column(Boolean, default=True)
    last_updated  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    price_bars    = relationship("PriceBar",    back_populates="ticker_ref")
    signals       = relationship("Signal",      back_populates="ticker_ref")
    fundamentals  = relationship("Fundamental", back_populates="ticker_ref", uselist=False)


class PriceBar(Base):
    """
    OHLCV bars — converted to TimescaleDB hypertable via Alembic migration.
    Partition key: ts (timestamp).  Time bucket: 1 minute.
    """
    __tablename__ = "price_bars"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", "timeframe", name="uq_price_bar"),
        Index("ix_price_bars_symbol_ts", "symbol", "ts"),
    )

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol     = Column(String(10), ForeignKey("tickers.symbol"), nullable=False)
    ts         = Column(DateTime, nullable=False)           # bar open time (UTC)
    timeframe  = Column(String(10), nullable=False)         # "1m","5m","1d"
    open       = Column(Float, nullable=False)
    high       = Column(Float, nullable=False)
    low        = Column(Float, nullable=False)
    close      = Column(Float, nullable=False)
    volume     = Column(BigInteger, nullable=False)
    vwap       = Column(Float)
    num_trades = Column(Integer)

    ticker_ref = relationship("Ticker", back_populates="price_bars")


class Signal(Base):
    """
    Computed trading signal for a ticker at a point in time.
    This is the core output of the quant engine.
    """
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_symbol_ts", "symbol", "ts"),
        Index("ix_signals_dip_score",  "dip_score"),
    )

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol            = Column(String(10), ForeignKey("tickers.symbol"), nullable=False)
    ts                = Column(DateTime, nullable=False, default=datetime.utcnow)

    # ── Dip sub-scores (0–100) ────────────────────────────────────────────────
    rsi_score         = Column(Float)          # RSI oversold contribution
    bb_score          = Column(Float)          # Bollinger Band position
    ma_deviation_score= Column(Float)          # Distance from MAs
    volume_score      = Column(Float)          # Volume spike on red day
    zscore_score      = Column(Float)          # Z-score vs historical mean
    mean_rev_score    = Column(Float)          # Mean reversion probability
    support_score     = Column(Float)          # Proximity to demand zone
    dip_score         = Column(Float)          # Composite (0–100)

    # ── Sentiment (0–100) ─────────────────────────────────────────────────────
    sentiment_score   = Column(Float)
    sentiment_label   = Column(String(20))     # positive / neutral / negative
    news_type         = Column(String(50))     # earnings / lawsuit / macro / etc

    # ── Market context ────────────────────────────────────────────────────────
    spy_trend         = Column(String(10))     # bullish / bearish / neutral
    sector_rs         = Column(Float)          # relative strength vs sector
    vix_level         = Column(Float)

    # ── Decision output ───────────────────────────────────────────────────────
    composite_score   = Column(Float)          # final 0–100
    bias              = Column(Enum(Bias))
    confidence        = Column(Float)          # 0–100 %
    reasoning         = Column(Text)           # human-readable explanation

    # ── Trade levels ─────────────────────────────────────────────────────────
    entry_low         = Column(Float)
    entry_high        = Column(Float)
    stop_loss         = Column(Float)
    target_1          = Column(Float)
    target_2          = Column(Float)
    risk_reward       = Column(Float)

    # ── Technical snapshot ───────────────────────────────────────────────────
    price             = Column(Float)
    rsi_14            = Column(Float)
    bb_pct            = Column(Float)          # % B position in Bollinger Bands
    ma20              = Column(Float)
    ma50              = Column(Float)
    ma200             = Column(Float)
    atr               = Column(Float)
    zscore            = Column(Float)

    # ── News refs ─────────────────────────────────────────────────────────────
    news_headlines    = Column(JSON)           # list of {title, url, source, ts}

    ticker_ref        = relationship("Ticker", back_populates="signals")


class Fundamental(Base):
    """Snapshot of fundamental data — refreshed daily."""
    __tablename__ = "fundamentals"

    symbol          = Column(String(10), ForeignKey("tickers.symbol"), primary_key=True)
    updated_at      = Column(DateTime, default=datetime.utcnow)
    pe_ratio        = Column(Float)
    pb_ratio        = Column(Float)
    ps_ratio        = Column(Float)
    ev_ebitda       = Column(Float)
    gross_margin    = Column(Float)
    revenue_growth  = Column(Float)          # YoY %
    eps_growth      = Column(Float)          # YoY %
    debt_to_equity  = Column(Float)
    current_ratio   = Column(Float)
    roe             = Column(Float)
    next_earnings   = Column(DateTime)       # upcoming earnings date
    raw             = Column(JSON)           # full FMP payload

    ticker_ref      = relationship("Ticker", back_populates="fundamentals")


class Watchlist(Base):
    """User-saved watchlist entries."""
    __tablename__ = "watchlists"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name        = Column(String(100), default="Default")
    symbol      = Column(String(10), ForeignKey("tickers.symbol"), nullable=False)
    added_at    = Column(DateTime, default=datetime.utcnow)
    notes       = Column(Text)


class BacktestResult(Base):
    """Stored backtest runs."""
    __tablename__ = "backtest_results"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_at          = Column(DateTime, default=datetime.utcnow)
    strategy_params = Column(JSON)
    start_date      = Column(DateTime)
    end_date        = Column(DateTime)
    universe        = Column(String(50))     # "SP500", "NASDAQ100", custom list
    total_trades    = Column(Integer)
    win_rate        = Column(Float)
    avg_return      = Column(Float)
    max_drawdown    = Column(Float)
    profit_factor   = Column(Float)
    sharpe_ratio    = Column(Float)
    equity_curve    = Column(JSON)           # list of {date, equity}
    trade_log       = Column(JSON)           # list of individual trades
