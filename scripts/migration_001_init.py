"""
Alembic migration: create all tables + convert OHLCV to TimescaleDB hypertable

Run with:
    alembic upgrade head
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid


def upgrade():
    # ── tickers ────────────────────────────────────────────────────────────────
    op.create_table(
        "tickers",
        sa.Column("symbol",       sa.String(10),  primary_key=True),
        sa.Column("name",         sa.String(200)),
        sa.Column("sector",       sa.String(100)),
        sa.Column("industry",     sa.String(150)),
        sa.Column("market_cap",   sa.BigInteger),
        sa.Column("avg_volume",   sa.BigInteger),
        sa.Column("exchange",     sa.String(20)),
        sa.Column("is_active",    sa.Boolean,     default=True),
        sa.Column("last_updated", sa.DateTime,    server_default=sa.func.now()),
    )

    # ── price_bars ─────────────────────────────────────────────────────────────
    op.create_table(
        "price_bars",
        sa.Column("id",         sa.BigInteger,  primary_key=True, autoincrement=True),
        sa.Column("symbol",     sa.String(10),  sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("ts",         sa.DateTime,    nullable=False),
        sa.Column("timeframe",  sa.String(10),  nullable=False),
        sa.Column("open",       sa.Float,       nullable=False),
        sa.Column("high",       sa.Float,       nullable=False),
        sa.Column("low",        sa.Float,       nullable=False),
        sa.Column("close",      sa.Float,       nullable=False),
        sa.Column("volume",     sa.BigInteger,  nullable=False),
        sa.Column("vwap",       sa.Float),
        sa.Column("num_trades", sa.Integer),
    )
    op.create_index("ix_price_bars_symbol_ts", "price_bars", ["symbol", "ts"])
    op.create_unique_constraint("uq_price_bar", "price_bars", ["symbol", "ts", "timeframe"])

    # Convert to TimescaleDB hypertable
    op.execute(
        "SELECT create_hypertable('price_bars', 'ts', if_not_exists => TRUE)"
    )
    # Compress chunks older than 7 days
    op.execute(
        "ALTER TABLE price_bars SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'symbol,timeframe')"
    )
    op.execute(
        "SELECT add_compression_policy('price_bars', INTERVAL '7 days')"
    )

    # ── signals ────────────────────────────────────────────────────────────────
    op.create_table(
        "signals",
        sa.Column("id",                 UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("symbol",             sa.String(10),  sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("ts",                 sa.DateTime,    nullable=False, server_default=sa.func.now()),
        sa.Column("rsi_score",          sa.Float),
        sa.Column("bb_score",           sa.Float),
        sa.Column("ma_deviation_score", sa.Float),
        sa.Column("volume_score",       sa.Float),
        sa.Column("zscore_score",       sa.Float),
        sa.Column("mean_rev_score",     sa.Float),
        sa.Column("support_score",      sa.Float),
        sa.Column("dip_score",          sa.Float),
        sa.Column("sentiment_score",    sa.Float),
        sa.Column("sentiment_label",    sa.String(20)),
        sa.Column("news_type",          sa.String(50)),
        sa.Column("spy_trend",          sa.String(10)),
        sa.Column("sector_rs",          sa.Float),
        sa.Column("vix_level",          sa.Float),
        sa.Column("composite_score",    sa.Float),
        sa.Column("bias",               sa.String(10)),
        sa.Column("confidence",         sa.Float),
        sa.Column("reasoning",          sa.Text),
        sa.Column("entry_low",          sa.Float),
        sa.Column("entry_high",         sa.Float),
        sa.Column("stop_loss",          sa.Float),
        sa.Column("target_1",           sa.Float),
        sa.Column("target_2",           sa.Float),
        sa.Column("risk_reward",        sa.Float),
        sa.Column("price",              sa.Float),
        sa.Column("rsi_14",             sa.Float),
        sa.Column("bb_pct",             sa.Float),
        sa.Column("ma20",               sa.Float),
        sa.Column("ma50",               sa.Float),
        sa.Column("ma200",              sa.Float),
        sa.Column("atr",                sa.Float),
        sa.Column("zscore",             sa.Float),
        sa.Column("news_headlines",     sa.JSON),
    )
    op.create_index("ix_signals_symbol_ts",  "signals", ["symbol", "ts"])
    op.create_index("ix_signals_dip_score",  "signals", ["dip_score"])
    op.create_index("ix_signals_composite",  "signals", ["composite_score"])

    # Convert signals to hypertable too
    op.execute(
        "SELECT create_hypertable('signals', 'ts', if_not_exists => TRUE)"
    )

    # ── fundamentals ──────────────────────────────────────────────────────────
    op.create_table(
        "fundamentals",
        sa.Column("symbol",        sa.String(10), sa.ForeignKey("tickers.symbol"), primary_key=True),
        sa.Column("updated_at",    sa.DateTime,   server_default=sa.func.now()),
        sa.Column("pe_ratio",      sa.Float),
        sa.Column("pb_ratio",      sa.Float),
        sa.Column("ps_ratio",      sa.Float),
        sa.Column("ev_ebitda",     sa.Float),
        sa.Column("gross_margin",  sa.Float),
        sa.Column("revenue_growth",sa.Float),
        sa.Column("eps_growth",    sa.Float),
        sa.Column("debt_to_equity",sa.Float),
        sa.Column("current_ratio", sa.Float),
        sa.Column("roe",           sa.Float),
        sa.Column("next_earnings", sa.DateTime),
        sa.Column("raw",           sa.JSON),
    )

    # ── watchlists ────────────────────────────────────────────────────────────
    op.create_table(
        "watchlists",
        sa.Column("id",       UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("name",     sa.String(100), default="Default"),
        sa.Column("symbol",   sa.String(10),  sa.ForeignKey("tickers.symbol"), nullable=False),
        sa.Column("added_at", sa.DateTime,    server_default=sa.func.now()),
        sa.Column("notes",    sa.Text),
    )

    # ── backtest_results ──────────────────────────────────────────────────────
    op.create_table(
        "backtest_results",
        sa.Column("id",              UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("run_at",          sa.DateTime,    server_default=sa.func.now()),
        sa.Column("strategy_params", sa.JSON),
        sa.Column("start_date",      sa.DateTime),
        sa.Column("end_date",        sa.DateTime),
        sa.Column("universe",        sa.String(50)),
        sa.Column("total_trades",    sa.Integer),
        sa.Column("win_rate",        sa.Float),
        sa.Column("avg_return",      sa.Float),
        sa.Column("max_drawdown",    sa.Float),
        sa.Column("profit_factor",   sa.Float),
        sa.Column("sharpe_ratio",    sa.Float),
        sa.Column("equity_curve",    sa.JSON),
        sa.Column("trade_log",       sa.JSON),
    )


def downgrade():
    for table in [
        "backtest_results", "watchlists", "fundamentals",
        "signals", "price_bars", "tickers",
    ]:
        op.drop_table(table)
