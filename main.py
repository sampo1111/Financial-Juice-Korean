from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from telegram import Update
from telegram.error import Conflict

from financial_juice_bot.bot import FinancialJuiceTelegramBot
from financial_juice_bot.config import load_settings
from financial_juice_bot.runtime import SingleInstanceError, SingleInstanceLock


def ensure_event_loop() -> None:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
        return

    if loop.is_closed():
        asyncio.set_event_loop(asyncio.new_event_loop())


def main() -> None:
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

