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


@dataclass(slots=True)
class NewsInsight:
    guid: str
    title: str
    translated_title: str
    explanation: str
    link: str
    published_at: datetime
    is_breaking: bool = False
