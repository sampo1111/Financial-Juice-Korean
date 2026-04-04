from __future__ import annotations

import logging

from dotenv import load_dotenv
from telegram import Update

from financial_juice_bot.bot import FinancialJuiceTelegramBot
from financial_juice_bot.config import load_settings


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )

    load_dotenv()
    settings = load_settings()

    bot = FinancialJuiceTelegramBot(settings)
    application = bot.build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

