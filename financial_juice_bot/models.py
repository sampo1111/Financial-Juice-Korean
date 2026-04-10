from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class NewsItem:
    guid: str
    title: str
    link: str
    published_at: datetime
    is_breaking: bool = False
    image_url: str | None = None


@dataclass(slots=True)
class NewsInsight:
    guid: str
    title: str
    translated_title: str
    explanation: str
    link: str
    published_at: datetime
    is_breaking: bool = False
    image_url: str | None = None


@dataclass(slots=True)
class Subscriber:
    chat_id: int
    chat_type: str
    label: str
    is_active: bool
    receive_card_posts: bool = False
    show_original: bool = True
    show_time: bool = True
