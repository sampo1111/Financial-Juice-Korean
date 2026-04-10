from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(slots=True)
class Settings:
    telegram_bot_token: str
    translator_engine: str
    deepl_api_key: str
    deepl_api_base_url: str
    deepl_source_lang: str
    deepl_target_lang: str
    financial_juice_rss_url: str
    poll_interval_seconds: int
    rss_min_fetch_interval_seconds: int
    rss_rate_limit_cooldown_seconds: int
    latest_limit: int
    request_timeout_seconds: float
    timezone: str
    database_path: Path


def load_settings() -> Settings:
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required. Set it in your .env file.")

    return Settings(
        telegram_bot_token=telegram_bot_token,
        translator_engine=os.getenv("TRANSLATOR_ENGINE", "deepl").strip() or "deepl",
        deepl_api_key=os.getenv("DEEPL_API_KEY", "").strip(),
        deepl_api_base_url=os.getenv("DEEPL_API_BASE_URL", "https://api-free.deepl.com").rstrip(
            "/"
        ),
        deepl_source_lang=os.getenv("DEEPL_SOURCE_LANG", "EN").strip() or "EN",
        deepl_target_lang=os.getenv("DEEPL_TARGET_LANG", "KO").strip() or "KO",
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
    )
