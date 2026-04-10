from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from .database import Database
from .models import NewsInsight, NewsItem
from .rss import FinancialJuiceFeedClient
from .translator_client import TranslationError


logger = logging.getLogger(__name__)


class TranslatorClient(Protocol):
    async def translate_and_explain(self, item: NewsItem) -> NewsInsight: ...

    async def aclose(self) -> None: ...


class NewsService:
    def __init__(
        self,
        database: Database,
        feed_client: FinancialJuiceFeedClient,
        translator_client: TranslatorClient,
    ) -> None:
        self.database = database
        self.feed_client = feed_client
        self.translator_client = translator_client
        self._sync_lock = asyncio.Lock()

    async def sync_latest_insights(self, limit: int) -> list[NewsInsight]:
        async with self._sync_lock:
            items = await self.feed_client.fetch_latest(limit=limit)
            insights: list[NewsInsight] = []
            for item in items:
                try:
                    insights.append(await self.ensure_insight(item))
                except TranslationError:
                    logger.exception("Failed to create insight for guid=%s", item.guid)
            return insights

    def get_latest_from_database(self, limit: int) -> list[NewsInsight]:
        return self.database.list_recent_processed_news(limit=limit)

    async def get_pending_broadcasts(self, lookback_limit: int) -> list[NewsInsight]:
        await self.sync_latest_insights(limit=lookback_limit)
        insights = self.database.list_recent_processed_news(limit=lookback_limit)
        insights.reverse()
        return insights

    def get_seed_guids(self, limit: int) -> list[str]:
        return self.database.list_recent_processed_guids(limit=limit)

    async def ensure_insight(self, item: NewsItem) -> NewsInsight:
        cached = self.database.get_processed_news(item.guid)
        if cached is not None:
            changed = False
            if item.is_breaking and not cached.is_breaking:
                cached.is_breaking = True
                changed = True
            if item.image_url and item.image_url != cached.image_url:
                cached.image_url = item.image_url
                changed = True
            if self._needs_translation_refresh(item, cached):
                refreshed = await self.translator_client.translate_and_explain(item)
                refreshed.is_breaking = item.is_breaking or cached.is_breaking
                refreshed.image_url = item.image_url or cached.image_url
                self.database.save_processed_news(refreshed)
                return refreshed
            if changed:
                self.database.save_processed_news(cached)
            return cached

        insight = await self.translator_client.translate_and_explain(item)
        self.database.save_processed_news(insight)
        return insight

    async def aclose(self) -> None:
        await self.feed_client.aclose()
        await self.translator_client.aclose()

    @staticmethod
    def _needs_translation_refresh(item: NewsItem, cached: NewsInsight) -> bool:
        title = item.title
        translated = cached.translated_title
        if not translated:
            return True

        for label in ("Actual", "Forecast", "Previous"):
            value = NewsService._extract_tagged_value(title, label)
            if value and value not in translated:
                return True

        if "MOM" in title.upper() and "전월" not in translated and "MoM" not in translated:
            return True
        if "YOY" in title.upper() and "전년" not in translated and "YoY" not in translated:
            return True
        if "QOQ" in title.upper() and "전분기" not in translated and "QoQ" not in translated:
            return True

        return False

    @staticmethod
    def _extract_tagged_value(title: str, label: str) -> str | None:
        import re

        match = re.search(
            rf"\b{label}\s+([A-Za-z0-9.+\-/%]+)",
            title,
            re.IGNORECASE,
        )
        if match is None:
            return None
        return match.group(1).rstrip(",)")
