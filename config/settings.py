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
        default=["SPY"],
        description="Tickers to monitor (SPY for political signals)"
    )

    # --- Polling intervals (seconds) ---
    truth_social_poll_interval: int = 30  # check Trump posts every 30s
    x_poll_interval: int = 30  # check X posts every 30s
    whitehouse_poll_interval: int = 60  # check White House every 60s
    finnhub_poll_interval: int = 30  # Finnhub political-filtered news
    alpha_vantage_poll_interval: int = 300  # Alpha Vantage (25/day free limit)

    # --- Signal thresholds ---
    # Sentiment score range: -1.0 (extreme bearish) to +1.0 (extreme bullish)
    buy_signal_threshold: float = 0.5
    sell_signal_threshold: float = -0.5
    min_confidence: float = 0.7  # minimum confidence to act on a signal
    signal_expiry_seconds: int = 600  # signals expire after 10 minutes

    # --- Signal quality filters ---
    min_sources: int = 2  # minimum articles required to trigger a signal
    min_agreement: float = 0.65  # 65% of articles must agree on direction
    signal_cooldown_seconds: int = 300  # 5 min cooldown before repeating same signal
    signal_flip_cooldown_seconds: int = 900  # 15 min cooldown before flipping BUY↔SELL

    # Trusted sources (Trump posts, WH statements) can trigger signals alone
    # if they meet this confidence threshold AND have HIGH impact.
    trusted_solo_min_confidence: float = 0.7

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8080
    webhook_secret: str = Field(default="change-me-in-production", description="Secret for webhook auth")

    # --- Notifications ---
    discord_webhook_url: str = Field(default="", description="Discord webhook URL for signal alerts")

    # --- LLM Scoring ---
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_max_tokens: int = 150

    model_config = {"env_file": str(ENV_FILE), "env_file_encoding": "utf-8"}


settings = Settings()
