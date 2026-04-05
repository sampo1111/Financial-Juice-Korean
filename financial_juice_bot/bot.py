from __future__ import annotations

import asyncio
from html import escape
import logging
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from .config import Settings
from .database import Database
from .models import NewsInsight
from .ollama_client import OllamaClient, OllamaError
from .rss import FeedFetchError, FinancialJuiceFeedClient
from .services import NewsService


logger = logging.getLogger(__name__)


class FinancialJuiceTelegramBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        self.feed_client = FinancialJuiceFeedClient(
            rss_url=settings.financial_juice_rss_url,
            timeout_seconds=settings.request_timeout_seconds,
            min_fetch_interval_seconds=settings.rss_min_fetch_interval_seconds,
            rate_limit_cooldown_seconds=settings.rss_rate_limit_cooldown_seconds,
        )
        self.ollama_client = OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            temperature=settings.ollama_temperature,
        )
        self.news_service = NewsService(
            database=self.database,
            feed_client=self.feed_client,
            ollama_client=self.ollama_client,
        )
        self.broadcast_lock = asyncio.Lock()

    def build_application(self) -> Application:
        application = (
            ApplicationBuilder()
            .token(self.settings.telegram_bot_token)
            .post_init(self.post_init)
            .post_shutdown(self.post_shutdown)
            .build()
        )

        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("subscribe", self.subscribe_command))
        application.add_handler(CommandHandler("unsubscribe", self.unsubscribe_command))
        application.add_handler(CommandHandler("latest", self.latest_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_error_handler(self.error_handler)
        return application

    async def post_init(self, application: Application) -> None:
        commands = [
            BotCommand("start", "봇 시작 및 구독"),
            BotCommand("latest", "저장된 최근 뉴스 보기"),
            BotCommand("status", "구독 상태 확인"),
            BotCommand("subscribe", "새 뉴스 자동 수신"),
            BotCommand("unsubscribe", "자동 수신 중지"),
            BotCommand("help", "도움말 보기"),
        ]
        await application.bot.set_my_commands(commands)

        try:
            await self.news_service.sync_latest_insights(limit=10)
        except (FeedFetchError, OllamaError):
            logger.exception("Initial Financial Juice sync failed.")

        if application.job_queue is None:
            raise RuntimeError(
                "JobQueue is not available. Install python-telegram-bot with the job-queue extra."
            )

        application.job_queue.run_repeating(
            self.broadcast_job,
            interval=self.settings.poll_interval_seconds,
            first=self.settings.poll_interval_seconds,
            name="financial-juice-poller",
        )

    async def post_shutdown(self, _: Application) -> None:
        await self.news_service.aclose()

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._subscribe_current_chat(update, seed_latest=True)
        await update.effective_message.reply_text(
            (
                "Financial Juice 실시간 헤드라인 구독을 시작했습니다.\n"
                "/latest 로 최근 저장된 뉴스 번역을 보고, 이후에는 새 헤드라인이 저장되면 자동으로 보내드립니다."
            )
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            (
                "사용 가능한 명령어\n"
                "/start - 현재 채팅을 구독합니다.\n"
                "/latest - 최근 저장된 Financial Juice 번역을 보여줍니다.\n"
                "/status - 현재 구독 상태와 설정을 확인합니다.\n"
                "/subscribe - 자동 수신을 켭니다.\n"
                "/unsubscribe - 자동 수신을 끕니다."
            )
        )

    async def subscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._subscribe_current_chat(update, seed_latest=True)
        await update.effective_message.reply_text("이 채팅은 이제 새 헤드라인을 자동으로 받습니다.")

    async def unsubscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat is None:
            return
        self.database.deactivate_subscriber(chat.id)
        await update.effective_message.reply_text(
            "자동 수신을 중지했습니다. 다시 받으려면 /subscribe 를 입력해 주세요."
        )

    async def latest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        limit = self._parse_latest_limit(context.args)
        message = update.effective_message
        if message is None:
            return

        insights = self.news_service.get_latest_from_database(limit=limit)
        if not insights:
            await message.reply_text(
                "아직 SQL에 저장된 뉴스가 없습니다. 봇이 한 번 동기화한 뒤 다시 시도해 주세요."
            )
            return

        for insight in insights:
            await message.reply_text(
                self._render_news_message(insight),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat is None or update.effective_message is None:
            return

        subscribed = self.database.is_active_subscriber(chat.id)
        stored_news = len(self.news_service.get_latest_from_database(limit=10))
        text = (
            f"구독 상태: {'ON' if subscribed else 'OFF'}\n"
            f"Ollama 모델: {self.settings.ollama_model}\n"
            f"Ollama 타임아웃: {int(self.settings.ollama_timeout_seconds)}초\n"
            f"폴링 주기: {self.settings.poll_interval_seconds}초\n"
            f"RSS 최소 재요청 간격: {self.settings.rss_min_fetch_interval_seconds}초\n"
            f"저장된 최근 뉴스 수: {stored_news}"
        )
        await update.effective_message.reply_text(text)

    async def broadcast_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.broadcast_lock.locked():
            logger.info("Previous broadcast job is still running. Skipping this cycle.")
            return

        async with self.broadcast_lock:
            chat_ids = self.database.list_active_chat_ids()
            if not chat_ids:
                return

            try:
                insights = await self.news_service.get_pending_broadcasts(lookback_limit=10)
            except (FeedFetchError, OllamaError):
                logger.exception("Failed to sync Financial Juice headlines.")
                return

            for insight in insights:
                for chat_id in chat_ids:
                    if self.database.has_sent_news(chat_id, insight.guid):
                        continue

                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=self._render_news_message(insight),
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    except Forbidden:
                        logger.warning("Chat %s blocked the bot. Subscription disabled.", chat_id)
                        self.database.deactivate_subscriber(chat_id)
                    except TelegramError:
                        logger.exception("Failed to send headline %s to chat %s", insight.guid, chat_id)
                    else:
                        self.database.mark_news_sent(chat_id, insight.guid)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception("Unhandled bot error", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message is not None:
            try:
                await update.effective_message.reply_text(
                    "처리 중 예기치 않은 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                )
            except TelegramError:
                logger.exception("Failed to send error message to Telegram.")

    async def _subscribe_current_chat(self, update: Update, seed_latest: bool) -> None:
        chat = update.effective_chat
        if chat is None:
            return

        label = chat.title or chat.full_name or str(chat.id)
        self.database.upsert_subscriber(chat.id, chat.type, label)

        if seed_latest:
            guids = self.news_service.get_seed_guids(limit=10)
            if guids:
                self.database.seed_sent_news(chat.id, guids)

    def _render_news_message(self, insight: NewsInsight) -> str:
        local_time = insight.published_at.astimezone(ZoneInfo(self.settings.timezone))
        time_text = local_time.strftime("%Y-%m-%d %H:%M %Z")
        return (
            f"<b>Financial Juice</b>\n"
            f"<b>원문</b>: {escape(insight.title)}\n"
            f"<b>번역</b>: {escape(insight.translated_title)}\n"
            f"<b>설명</b>: {escape(insight.explanation)}\n"
            f"<b>시간</b>: {escape(time_text)}\n"
            f"<a href=\"{escape(insight.link, quote=True)}\">원문 링크</a>"
        )

    def _parse_latest_limit(self, args: list[str]) -> int:
        if not args:
            return self.settings.latest_limit

        try:
            value = int(args[0])
        except ValueError:
            return self.settings.latest_limit

        return max(1, min(5, value))
