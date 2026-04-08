"""
Configuration for News Trading Signal system.
API keys loaded from environment variables / .env file.
"""

from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field

# Resolve .env relative to project root (parent of config/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    # --- API Keys (all optional — system uses whichever are configured) ---
    finnhub_api_key: str = Field(default="", description="Finnhub API key (free tier available)")
    alpha_vantage_api_key: str = Field(default="", description="Alpha Vantage API key (free tier: 25 req/day)")
    fmp_api_key: str = Field(default="", description="Financial Modeling Prep API key (free tier available)")
    anthropic_api_key: str = Field(default="", description="Anthropic API key for Claude Haiku sentiment scoring")

    # --- Tickers to monitor ---
    watch_tickers: list[str] = Field(
        default=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "SPY", "QQQ", "ES"],
        description="Tickers to monitor for news"
    )

    # --- Polling intervals (seconds) ---
    finnhub_poll_interval: int = 30
    alpha_vantage_poll_interval: int = 300  # conservative due to 25/day free limit
    fmp_poll_interval: int = 60
    rss_poll_interval: int = 60

    # --- Signal thresholds ---
    # Sentiment score range: -1.0 (extreme bearish) to +1.0 (extreme bullish)
    buy_signal_threshold: float = 0.4
    sell_signal_threshold: float = -0.4
    min_confidence: float = 0.6  # minimum confidence to act on a signal
    signal_expiry_seconds: int = 300  # signals expire after 5 minutes

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8080
    webhook_secret: str = Field(default="change-me-in-production", description="Secret for webhook auth")

    # --- LLM Scoring ---
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_max_tokens: int = 150

    model_config = {"env_file": str(ENV_FILE), "env_file_encoding": "utf-8"}


settings = Settings()
