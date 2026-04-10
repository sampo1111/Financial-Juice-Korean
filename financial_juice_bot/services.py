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
            if item.is_breaking and not cached.is_breaking:
                cached.is_breaking = True
                self.database.save_processed_news(cached)
            return cached

        insight = await self.translator_client.translate_and_explain(item)
        self.database.save_processed_news(insight)
        return insight

    async def aclose(self) -> None:
        await self.feed_client.aclose()
        await self.translator_client.aclose()
