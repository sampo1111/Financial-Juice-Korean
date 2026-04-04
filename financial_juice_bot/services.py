from __future__ import annotations

import asyncio

from .database import Database
from .models import NewsInsight, NewsItem
from .ollama_client import OllamaClient
from .rss import FinancialJuiceFeedClient


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
                insights.append(await self.ensure_insight(item))
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
            return cached

        insight = await self.ollama_client.translate_and_explain(item)
        self.database.save_processed_news(insight)
        return insight

    async def aclose(self) -> None:
        await self.feed_client.aclose()
        await self.ollama_client.aclose()
