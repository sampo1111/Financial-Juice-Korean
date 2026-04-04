from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import time
import xml.etree.ElementTree as ET

import httpx

from .models import NewsItem


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


class FeedFetchError(RuntimeError):
    """Raised when the Financial Juice RSS feed cannot be fetched."""


class FinancialJuiceFeedClient:
    def __init__(
        self,
        rss_url: str,
        timeout_seconds: float,
        min_fetch_interval_seconds: int = 90,
        rate_limit_cooldown_seconds: int = 180,
    ) -> None:
        self.rss_url = rss_url
        self.min_fetch_interval_seconds = min_fetch_interval_seconds
        self.rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._cache: list[NewsItem] = []
        self._last_fetch_monotonic: float | None = None
        self._rate_limited_until: float | None = None
        self._lock = asyncio.Lock()

    async def fetch_latest(self, limit: int = 10) -> list[NewsItem]:
        async with self._lock:
            now = time.monotonic()
            cached_items = self._cache[:limit]

            if (
                cached_items
                and self._last_fetch_monotonic is not None
                and now - self._last_fetch_monotonic < self.min_fetch_interval_seconds
            ):
                return cached_items

            if (
                cached_items
                and self._rate_limited_until is not None
                and now < self._rate_limited_until
            ):
                return cached_items

            try:
                response = await self._client.get(self.rss_url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    retry_after = self._parse_retry_after(exc.response.headers.get("Retry-After"))
                    cooldown_seconds = retry_after or self.rate_limit_cooldown_seconds
                    self._rate_limited_until = now + cooldown_seconds
                    if cached_items:
                        return cached_items
                    raise FeedFetchError(
                        "Financial Juice RSS temporarily rate-limited this bot. "
                        f"Please wait about {cooldown_seconds} seconds and try again."
                    ) from exc
                raise FeedFetchError(f"Financial Juice RSS fetch failed: {exc}") from exc
            except httpx.HTTPError as exc:
                if cached_items:
                    return cached_items
                raise FeedFetchError(f"Financial Juice RSS fetch failed: {exc}") from exc

            try:
                items = self._parse_items(response.text, limit=limit)
            except ET.ParseError as exc:
                if cached_items:
                    return cached_items
                raise FeedFetchError("Financial Juice RSS feed returned invalid XML.") from exc

            self._cache = items
            self._last_fetch_monotonic = now
            self._rate_limited_until = None
            return items[:limit]

    def get_cached_latest(self, limit: int = 10) -> list[NewsItem]:
        return self._cache[:limit]

    async def aclose(self) -> None:
        await self._client.aclose()

    def _parse_items(self, xml_text: str, limit: int) -> list[NewsItem]:
        root = ET.fromstring(xml_text)
        items: list[NewsItem] = []
        for node in root.findall("./channel/item"):
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            guid = (node.findtext("guid") or link or title).strip()
            pub_date = (node.findtext("pubDate") or "").strip()

            if not title or not link:
                continue

            published_at = parsedate_to_datetime(pub_date) if pub_date else None
            if published_at is None:
                published_at = datetime.now(tz=UTC)
            elif published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)

            items.append(
                NewsItem(
                    guid=guid,
                    title=title.replace("FinancialJuice: ", "", 1).strip(),
                    link=link,
                    published_at=published_at,
                )
            )

            if len(items) >= limit:
                break

        return items

    @staticmethod
    def _parse_retry_after(raw_value: str | None) -> int | None:
        if raw_value is None:
            return None

        try:
            value = int(raw_value.strip())
        except ValueError:
            return None
        return max(1, value)
