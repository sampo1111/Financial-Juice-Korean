from __future__ import annotations

import asyncio
import logging
import sys

from dotenv import load_dotenv
from telegram import Update
from telegram.error import Conflict

from financial_juice_bot.bot import FinancialJuiceTelegramBot
from financial_juice_bot.config import load_settings
from financial_juice_bot.runtime import SingleInstanceError, SingleInstanceLock


MIN_PYTHON = (3, 10)


def ensure_event_loop() -> None:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
        return

    if loop.is_closed():
        asyncio.set_event_loop(asyncio.new_event_loop())


def main() -> None:
    if sys.version_info < MIN_PYTHON:
        required = ".".join(str(part) for part in MIN_PYTHON)
        current = ".".join(str(part) for part in sys.version_info[:3])
        raise SystemExit(
            f"This project requires Python {required}+.\n"
            f"Current interpreter: Python {current}\n"
            "Install Python 3.10 or newer on the server and run the bot again."
        )

    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )

    ensure_event_loop()
    load_dotenv()
    settings = load_settings()

    try:
        with SingleInstanceLock(settings.database_path.with_suffix(".lock")):
            bot = FinancialJuiceTelegramBot(settings)
            application = bot.build_application()
            application.run_polling(allowed_updates=Update.ALL_TYPES)
    except SingleInstanceError as exc:
        raise SystemExit(str(exc)) from exc
    except Conflict as exc:
        raise SystemExit(
            "Telegram rejected polling because another bot instance is already using getUpdates with this token."
        ) from exc


if __name__ == "__main__":
    main()

