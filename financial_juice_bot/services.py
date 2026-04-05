from __future__ import annotations

import asyncio
import logging

from .database import Database
from .models import NewsInsight, NewsItem
from .ollama_client import OllamaClient, OllamaError
from .rss import FinancialJuiceFeedClient


logger = logging.getLogger(__name__)


class NewsService:
    def __init__(
        self,
        database: Database,
        feed_client: FinancialJuiceFeedClient,
        ollama_client: OllamaClient,
    ) -> None:
        self.database = database
        self.feed_client = feed_client
        self.ollama_client = ollama_client
        self._sync_lock = asyncio.Lock()

    async def sync_latest_insights(self, limit: int) -> list[NewsInsight]:
        async with self._sync_lock:
            items = await self.feed_client.fetch_latest(limit=limit)
            insights: list[NewsInsight] = []
            for item in items:
                try:
                    insights.append(await self.ensure_insight(item))
                except OllamaError:
                    logger.exception("Failed to create insight for guid=%s", item.guid)
            return insights

    def get_latest_from_database(self, limit: int) -> list[NewsInsight]:
        return self.database.list_recent_processed_news(limit=limit)

    async def refresh_recent_cached_insights(self, limit: int) -> list[NewsInsight]:
        async with self._sync_lock:
            refreshed: list[NewsInsight] = []
            for insight in self.database.list_recent_processed_news(limit=limit):
                if not self._needs_explanation_refresh(insight):
                    refreshed.append(insight)
                    continue

                item = NewsItem(
                    guid=insight.guid,
                    title=insight.title,
                    link=insight.link,
                    published_at=insight.published_at,
                )
                refreshed_insight = await self.ollama_client.translate_and_explain(item)
                self.database.save_processed_news(refreshed_insight)
                refreshed.append(refreshed_insight)
            return refreshed

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
            if not self._needs_explanation_refresh(cached):
                return cached

            logger.info("Refreshing cached insight format for guid=%s", item.guid)
            refreshed = await self.ollama_client.translate_and_explain(item)
            self.database.save_processed_news(refreshed)
            return refreshed

        insight = await self.ollama_client.translate_and_explain(item)
        self.database.save_processed_news(insight)
        return insight

    @staticmethod
    def _needs_explanation_refresh(insight: NewsInsight) -> bool:
        explanation = insight.explanation
        return "자산 영향:" not in explanation or "종합:" not in explanation

    async def aclose(self) -> None:
        await self.feed_client.aclose()
        await self.ollama_client.aclose()
