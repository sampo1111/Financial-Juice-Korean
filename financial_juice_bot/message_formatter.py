from __future__ import annotations

from datetime import datetime
from html import escape
import re
from zoneinfo import ZoneInfo

from .content_filter import is_card_post
from .models import NewsInsight


ACTUAL_PATTERN = re.compile(r"\bActual\s+(?P<actual>[^(),]+)", re.IGNORECASE)
FORECAST_PATTERN = re.compile(r"\bForecast\s+(?P<forecast>[^(),]+)", re.IGNORECASE)
PREVIOUS_PATTERN = re.compile(r"\bPrevious\s+(?P<previous>[^(),]+)", re.IGNORECASE)


def render_news_message(insight: NewsInsight, timezone: str) -> str:
    if is_card_post(insight.title):
        return _render_card_message(insight, timezone)
    if _is_indicator_release(insight.title):
        return _render_indicator_message(insight, timezone)
    return _render_general_message(insight, timezone)


def _render_indicator_message(insight: NewsInsight, timezone: str) -> str:
    header = "<b>[속보][지표]</b>" if insight.is_breaking else "<b>[지표]</b>"
    summary_title = _strip_trailing_parenthetical(insight.translated_title)
    stats = _build_stats_line(insight.title)

    lines = [f"{header} {escape(summary_title)}"]
    if stats:
        lines.append(escape(stats))
    lines.extend(
        [
            f"<code>{escape(_format_time(insight, timezone))}</code>",
            _render_original_line(insight.title),
            f"<a href=\"{escape(insight.link, quote=True)}\">링크</a>",
        ]
    )
    return "\n".join(lines)


def _render_general_message(insight: NewsInsight, timezone: str) -> str:
    header = "<b>[속보]</b>" if insight.is_breaking else "<b>[뉴스]</b>"
    return "\n".join(
        [
            f"{header} {escape(insight.translated_title)}",
            f"<code>{escape(_format_time(insight, timezone))}</code>",
            _render_original_line(insight.title),
            f"<a href=\"{escape(insight.link, quote=True)}\">링크</a>",
        ]
    )


def _render_card_message(insight: NewsInsight, timezone: str) -> str:
    header = "<b>[속보][카드]</b>" if insight.is_breaking else "<b>[카드]</b>"
    return "\n".join(
        [
            f"{header} {escape(insight.translated_title)}",
            f"<code>{escape(_format_time(insight, timezone))}</code>",
            _render_original_line(insight.title),
            f"<a href=\"{escape(insight.link, quote=True)}\">링크</a>",
        ]
    )


def _render_original_line(title: str) -> str:
    return f"원문 <tg-spoiler>{escape(title)}</tg-spoiler>"


def _is_indicator_release(title: str) -> bool:
    uppercase = f" {title.upper()} "
    return " ACTUAL " in uppercase


def _build_stats_line(title: str) -> str:
    parts: list[str] = []
    actual = _extract_value(ACTUAL_PATTERN, title)
    forecast = _extract_value(FORECAST_PATTERN, title)
    previous = _extract_value(PREVIOUS_PATTERN, title)

    if actual:
        parts.append(f"실제 {actual}")
    if forecast:
        parts.append(f"예상 {forecast}")
    if previous:
        parts.append(f"이전 {previous}")
    return " | ".join(parts)


def _extract_value(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    return " ".join(match.group(1).split())


def _strip_trailing_parenthetical(text: str) -> str:
    normalized = " ".join(text.split())
    return re.sub(r"\s*\([^()]*\)\s*$", "", normalized).strip()


def _format_time(insight: NewsInsight, timezone: str) -> str:
    zone = ZoneInfo(timezone)
    local_time = insight.published_at.astimezone(zone)
    today = datetime.now(zone).date()
    if local_time.date() == today:
        return local_time.strftime("%H:%M %Z")
    return local_time.strftime("%m-%d %H:%M %Z")
