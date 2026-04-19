"""Central config — reads from environment / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # DB
    DATABASE_URL: str = "postgresql+asyncpg://quant:quant_secret@localhost:5432/quant_db"
    SYNC_DATABASE_URL: str = "postgresql://quant:quant_secret@localhost:5432/quant_db"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # API Keys
    POLYGON_API_KEY: str = ""
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"
    NEWS_API_KEY: str = ""
    BENZINGA_API_KEY: str = ""
    FMP_API_KEY: str = ""

    # App
    SECRET_KEY: str = "dev_secret_change_in_production"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Scan settings
    SCAN_INTERVAL_SECONDS: int = 300
    WATCHLIST_SCAN_SECONDS: int = 60
    MIN_MARKET_CAP: int = 500_000_000
    MIN_AVG_VOLUME: int = 1_000_000
    MIN_PRICE: float = 5.0
    MAX_TICKERS_PER_SCAN: int = 500

    # Strategy defaults
    DIP_SCORE_THRESHOLD: float = 55.0
    SENTIMENT_SCORE_THRESHOLD: float = 35.0
    MIN_CONFIDENCE: float = 55.0

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
