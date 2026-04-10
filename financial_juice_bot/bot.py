from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, Message, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from .config import Settings
from .content_filter import is_card_post
from .database import Database
from .message_formatter import render_news_message
from .models import NewsInsight, Subscriber
from .rss import FeedFetchError, FinancialJuiceFeedClient
from .services import NewsService
from .translator_client import DeepLTranslateClient, TranslationError


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
        self.translator_client = DeepLTranslateClient(
            api_key=settings.deepl_api_key,
            base_url=settings.deepl_api_base_url,
            source_lang=settings.deepl_source_lang,
            target_lang=settings.deepl_target_lang,
            timeout_seconds=settings.request_timeout_seconds,
        )
        self.news_service = NewsService(
            database=self.database,
            feed_client=self.feed_client,
            translator_client=self.translator_client,
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
        application.add_handler(CommandHandler("cards", self.cards_command))
        application.add_error_handler(self.error_handler)
        return application

    async def post_init(self, application: Application) -> None:
        commands = [
            BotCommand("start", "구독 시작"),
            BotCommand("latest", "최근 뉴스 보기"),
            BotCommand("status", "구독 상태 확인"),
            BotCommand("subscribe", "자동 수신 켜기"),
            BotCommand("unsubscribe", "자동 수신 끄기"),
            BotCommand("cards", "카드형 게시물 설정"),
            BotCommand("help", "명령어 보기"),
        ]
        await application.bot.set_my_commands(commands)

        try:
            await self.news_service.sync_latest_insights(limit=10)
        except (FeedFetchError, TranslationError):
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
        if update.effective_message is None:
            return
        await update.effective_message.reply_text(
            (
                "Financial Juice 헤드라인 구독을 시작했습니다.\n"
                "/latest 로 최근 뉴스를 확인할 수 있고, 이후 새 헤드라인은 자동으로 보내드립니다.\n"
                "카드형 게시물은 기본적으로 꺼져 있습니다. 필요하면 /cards on 을 입력해 주세요."
            )
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None:
            return
        await update.effective_message.reply_text(
            (
                "사용 가능한 명령어\n"
                "/start - 현재 채팅을 구독합니다.\n"
                "/latest - 최근 Financial Juice 뉴스를 보여줍니다.\n"
                "/latest 5 - 최근 5개까지 확인합니다.\n"
                "/status - 현재 구독 상태를 확인합니다.\n"
                "/subscribe - 자동 수신을 켭니다.\n"
                "/unsubscribe - 자동 수신을 끕니다.\n"
                "/cards - 카드형 게시물 수신 상태를 봅니다.\n"
                "/cards on - 금리 확률/변동성/상관행렬 카드도 받습니다.\n"
                "/cards off - 일반 뉴스만 받습니다."
            )
        )

    async def subscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._subscribe_current_chat(update, seed_latest=True)
        if update.effective_message is None:
            return
        await update.effective_message.reply_text(
            "이 채팅은 이제 새 헤드라인을 자동으로 받습니다."
        )

    async def unsubscribe_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        if chat is None or update.effective_message is None:
            return
        self.database.deactivate_subscriber(chat.id)
        await update.effective_message.reply_text(
            "자동 수신을 중지했습니다. 다시 받으려면 /subscribe 를 입력해 주세요."
        )

    async def latest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None:
            return

        limit = self._parse_latest_limit(context.args)
        chat = update.effective_chat
        subscriber = self.database.get_subscriber(chat.id) if chat is not None else None
        insights = self.news_service.get_latest_from_database(limit=max(limit * 5, 20))
        insights = self._filter_insights_for_subscriber(insights, subscriber)[:limit]

        if not insights:
            await message.reply_text(
                "지금 보여드릴 뉴스가 없습니다. 카드형 게시물을 켜려면 /cards on 을 입력해 주세요."
            )
            return

        for insight in insights:
            await self._reply_with_insight(message, insight)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat is None or update.effective_message is None:
            return

        subscriber = self.database.get_subscriber(chat.id)
        subscribed = bool(subscriber and subscriber.is_active)
        receive_card_posts = bool(subscriber and subscriber.receive_card_posts)
        stored_news = len(self.news_service.get_latest_from_database(limit=10))

        text = (
            f"구독 상태: {'ON' if subscribed else 'OFF'}\n"
            f"카드형 게시물: {'ON' if receive_card_posts else 'OFF'}\n"
            f"번역 엔진: {self.settings.translator_engine}\n"
            f"언어쌍: {self.settings.deepl_source_lang} -> {self.settings.deepl_target_lang}\n"
            f"폴링 주기: {self.settings.poll_interval_seconds}초\n"
            f"RSS 최소 재요청 간격: {self.settings.rss_min_fetch_interval_seconds}초\n"
            f"저장된 최근 뉴스 수: {stored_news}"
        )
        await update.effective_message.reply_text(text)

    async def cards_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return

        subscriber = self.database.get_subscriber(chat.id)
        if subscriber is None:
            await message.reply_text(
                "먼저 /start 또는 /subscribe 로 구독을 만든 뒤 사용해 주세요."
            )
            return

        if not context.args:
            state = "ON" if subscriber.receive_card_posts else "OFF"
            await message.reply_text(
                (
                    f"카드형 게시물 수신: {state}\n"
                    "대상: Interest Rate Probabilities, Implied Volatility, Correlation Matrix, Currency Strength Chart\n"
                    "변경: /cards on 또는 /cards off"
                )
            )
            return

        option = context.args[0].strip().lower()
        if option not in {"on", "off"}:
            await message.reply_text("사용법: /cards on 또는 /cards off")
            return

        enabled = option == "on"
        self.database.set_receive_card_posts(chat.id, enabled)

        if enabled:
            await message.reply_text(
                "카드형 게시물 수신을 켰습니다. 이제 금리 확률, 변동성, 상관행렬 카드도 함께 받습니다."
            )
            return

        await message.reply_text(
            "카드형 게시물 수신을 껐습니다. 이제 일반 뉴스형 헤드라인만 보냅니다."
        )

    async def broadcast_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.broadcast_lock.locked():
            logger.info("Previous broadcast job is still running. Skipping this cycle.")
            return

        async with self.broadcast_lock:
            subscribers = self.database.list_active_subscribers()
            if not subscribers:
                return

            try:
                insights = await self.news_service.get_pending_broadcasts(lookback_limit=10)
            except (FeedFetchError, TranslationError):
                logger.exception("Failed to sync Financial Juice headlines.")
                return

            for insight in insights:
                for subscriber in subscribers:
                    if self.database.has_sent_news(subscriber.chat_id, insight.guid):
                        continue

                    if not self._should_deliver_to_subscriber(insight, subscriber):
                        self.database.mark_news_sent(subscriber.chat_id, insight.guid)
                        continue

                    try:
                        await self._send_insight(context, subscriber.chat_id, insight)
                    except Forbidden:
                        logger.warning(
                            "Chat %s blocked the bot. Subscription disabled.",
                            subscriber.chat_id,
                        )
                        self.database.deactivate_subscriber(subscriber.chat_id)
                    except TelegramError:
                        logger.exception(
                            "Failed to send headline %s to chat %s",
                            insight.guid,
                            subscriber.chat_id,
                        )
                    else:
                        self.database.mark_news_sent(subscriber.chat_id, insight.guid)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception("Unhandled bot error", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message is not None:
            try:
                await update.effective_message.reply_text(
                    "처리 중 예기치 못한 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
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

    async def _reply_with_insight(self, message: Message, insight: NewsInsight) -> None:
        text = self._render_news_message(insight)
        if insight.image_url:
            try:
                await message.reply_photo(
                    photo=insight.image_url,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                )
                return
            except TelegramError:
                logger.exception("Failed to send photo reply for guid=%s", insight.guid)

        await message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=bool(insight.image_url),
        )

    async def _send_insight(
        self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, insight: NewsInsight
    ) -> None:
        text = self._render_news_message(insight)
        if insight.image_url:
            try:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=insight.image_url,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                )
                return
            except TelegramError:
                logger.exception(
                    "Failed to send headline photo %s to chat %s",
                    insight.guid,
                    chat_id,
                )

        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=bool(insight.image_url),
        )

    def _render_news_message(self, insight: NewsInsight) -> str:
        return render_news_message(insight, self.settings.timezone)

    @staticmethod
    def _filter_insights_for_subscriber(
        insights: list[NewsInsight],
        subscriber: Subscriber | None,
    ) -> list[NewsInsight]:
        if subscriber is not None and subscriber.receive_card_posts:
            return insights
        return [insight for insight in insights if not is_card_post(insight.title)]

    @staticmethod
    def _should_deliver_to_subscriber(insight: NewsInsight, subscriber: Subscriber) -> bool:
        return subscriber.receive_card_posts or not is_card_post(insight.title)

    def _parse_latest_limit(self, args: list[str]) -> int:
        if not args:
            return self.settings.latest_limit

        try:
            value = int(args[0])
        except ValueError:
            return self.settings.latest_limit

        return max(1, min(5, value))
