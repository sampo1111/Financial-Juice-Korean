from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(slots=True)
class Settings:
    telegram_bot_token: str
    ollama_model: str
    ollama_base_url: str
    financial_juice_rss_url: str
    poll_interval_seconds: int
    rss_min_fetch_interval_seconds: int
    rss_rate_limit_cooldown_seconds: int
    latest_limit: int
    request_timeout_seconds: float
    timezone: str
    database_path: Path
    ollama_temperature: float


def load_settings() -> Settings:
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required. Set it in your .env file.")

    return Settings(
        telegram_bot_token=telegram_bot_token,
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3:8b").strip() or "llama3:8b",
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        financial_juice_rss_url=os.getenv(
            "FINANCIAL_JUICE_RSS_URL",
            "https://www.financialjuice.com/feed.ashx?xy=rss",
        ).strip(),
        poll_interval_seconds=max(15, int(os.getenv("POLL_INTERVAL_SECONDS", "60"))),
        rss_min_fetch_interval_seconds=max(
            30, int(os.getenv("RSS_MIN_FETCH_INTERVAL_SECONDS", "90"))
        ),
        rss_rate_limit_cooldown_seconds=max(
            60, int(os.getenv("RSS_RATE_LIMIT_COOLDOWN_SECONDS", "180"))
        ),
        latest_limit=max(1, min(5, int(os.getenv("LATEST_LIMIT", "3")))),
        request_timeout_seconds=max(5.0, float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))),
        timezone=os.getenv("BOT_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul",
        database_path=Path(os.getenv("DATABASE_PATH", "bot.db")).expanduser().resolve(),
        ollama_temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0.1")),
    )
