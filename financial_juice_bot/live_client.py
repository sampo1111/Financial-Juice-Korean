from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
import time
import xml.etree.ElementTree as ET

import httpx


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)

BOOTSTRAP_PATTERN = re.compile(
    r"var MainURLData='([^']+)';.*?var info = '([^']+)';",
    re.DOTALL,
)


class LiveFeedError(RuntimeError):
    """Raised when the Financial Juice live feed cannot be fetched."""


@dataclass(slots=True)
class LiveHeadline:
    title: str
    link: str
    is_breaking: bool


@dataclass(slots=True)
class _LiveBootstrap:
    startup_url: str
    info_token: str


class FinancialJuiceLiveClient:
    def __init__(
        self,
        home_url: str = "https://www.financialjuice.com/home",
        timeout_seconds: float = 20.0,
        min_fetch_interval_seconds: int = 90,
        bootstrap_ttl_seconds: int = 1800,
    ) -> None:
        self.home_url = home_url
        self.min_fetch_interval_seconds = min_fetch_interval_seconds
        self.bootstrap_ttl_seconds = bootstrap_ttl_seconds
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": home_url,
            },
        )
        self._bootstrap: _LiveBootstrap | None = None
        self._bootstrap_expires_at: float | None = None
        self._cache: list[LiveHeadline] = []
        self._last_fetch_monotonic: float | None = None

    async def fetch_latest(self, limit: int = 20) -> list[LiveHeadline]:
        now = time.monotonic()
        cached_items = self._cache[:limit]
        if (
            cached_items
            and self._last_fetch_monotonic is not None
            and now - self._last_fetch_monotonic < self.min_fetch_interval_seconds
        ):
            return cached_items

        bootstrap = await self._get_bootstrap(now)
        payload = {
            "info": bootstrap.info_token,
            "TimeOffset": self._time_offset_seconds(),
            "tabID": 0,
            "oldID": 0,
            "TickerID": 0,
            "FeedCompanyID": 0,
            "strSearch": "",
            "extraNID": 0,
        }

        try:
            response = await self._client.post(bootstrap.startup_url, data=payload)
            response.raise_for_status()
            headlines = self._parse_startup_response(response.text, limit=limit)
        except (httpx.HTTPError, ET.ParseError, json.JSONDecodeError, KeyError, ValueError) as exc:
            if cached_items:
                return cached_items
            raise LiveFeedError(f"Financial Juice live feed fetch failed: {exc}") from exc

        self._cache = headlines
        self._last_fetch_monotonic = now
        return headlines[:limit]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_bootstrap(self, now: float) -> _LiveBootstrap:
        if (
            self._bootstrap is not None
            and self._bootstrap_expires_at is not None
            and now < self._bootstrap_expires_at
        ):
            return self._bootstrap

        try:
            response = await self._client.get(self.home_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LiveFeedError(f"Financial Juice home bootstrap failed: {exc}") from exc

        match = BOOTSTRAP_PATTERN.search(response.text)
        if match is None:
            raise LiveFeedError("Financial Juice home page did not expose live bootstrap data.")

        main_url, info_token = match.groups()
        startup_url = f"{main_url.rstrip('/')}/FJService.asmx/Startup"
        bootstrap = _LiveBootstrap(startup_url=startup_url, info_token=info_token)
        self._bootstrap = bootstrap
        self._bootstrap_expires_at = now + self.bootstrap_ttl_seconds
        return bootstrap

    def _parse_startup_response(self, xml_text: str, limit: int) -> list[LiveHeadline]:
        root = ET.fromstring(xml_text)
        payload = "".join(root.itertext()).strip()
        data = json.loads(payload)
        news_items = data.get("News", [])
        headlines: list[LiveHeadline] = []
        for node in news_items:
            title = str(node.get("Title", "")).strip()
            link = str(node.get("EURL", "")).strip()
            if not title or not link:
                continue

            headlines.append(
                LiveHeadline(
                    title=title,
                    link=link,
                    is_breaking=bool(node.get("Breaking")),
                )
            )

            if len(headlines) >= limit:
                break

        return headlines

    @staticmethod
    def _time_offset_seconds() -> int:
        current = datetime.now().astimezone()
        offset = current.utcoffset()
        if offset is None:
            return 0
        return int(offset.total_seconds())
