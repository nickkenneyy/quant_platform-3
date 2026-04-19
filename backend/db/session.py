"""Async SQLAlchemy engine + session factory."""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from typing import AsyncGenerator
import structlog

from core.config import settings

logger = structlog.get_logger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    echo=settings.ENVIRONMENT == "development",
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session and handles rollback on error."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_hypertable(conn, table_name: str, time_column: str = "ts") -> None:
    """Convert a regular PG table to a TimescaleDB hypertable."""
    await conn.execute(
        text(
            f"SELECT create_hypertable('{table_name}', '{time_column}', "
            f"if_not_exists => TRUE, migrate_data => TRUE)"
        )
    )
    logger.info("hypertable_created", table=table_name)
