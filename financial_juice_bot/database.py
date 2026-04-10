from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from .models import NewsInsight, Subscriber


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id INTEGER PRIMARY KEY,
                    chat_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    receive_card_posts INTEGER NOT NULL DEFAULT 0,
                    show_original INTEGER NOT NULL DEFAULT 0,
                    show_time INTEGER NOT NULL DEFAULT 1,
                    show_link INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_news (
                    guid TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    translated_title TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    is_breaking INTEGER NOT NULL DEFAULT 0,
                    image_url TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sent_news (
                    guid TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (guid, chat_id)
                );
                """
            )
            self._ensure_subscriber_columns(conn)
            self._ensure_processed_news_columns(conn)

    def upsert_subscriber(self, chat_id: int, chat_type: str, label: str) -> None:
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subscribers (
                    chat_id, chat_type, label, is_active, receive_card_posts,
                    show_original, show_time, show_link, created_at, updated_at
                )
                VALUES (?, ?, ?, 1, 0, 0, 1, 0, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_type = excluded.chat_type,
                    label = excluded.label,
                    is_active = 1,
                    updated_at = excluded.updated_at
                """,
                (chat_id, chat_type, label, now, now),
            )

    def deactivate_subscriber(self, chat_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE subscribers SET is_active = 0, updated_at = ? WHERE chat_id = ?",
                (self._now(), chat_id),
            )

    def get_subscriber(self, chat_id: int) -> Subscriber | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT chat_id, chat_type, label, is_active, receive_card_posts, show_original, show_time, show_link
                FROM subscribers
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()

        if row is None:
            return None

        return Subscriber(
            chat_id=int(row["chat_id"]),
            chat_type=str(row["chat_type"]),
            label=str(row["label"]),
            is_active=bool(row["is_active"]),
            receive_card_posts=bool(row["receive_card_posts"]),
            show_original=bool(row["show_original"]),
            show_time=bool(row["show_time"]),
            show_link=bool(row["show_link"]),
        )

    def set_receive_card_posts(self, chat_id: int, enabled: bool) -> None:
        self._update_subscriber_flag(chat_id, "receive_card_posts", enabled)

    def set_show_original(self, chat_id: int, enabled: bool) -> None:
        self._update_subscriber_flag(chat_id, "show_original", enabled)

    def set_show_time(self, chat_id: int, enabled: bool) -> None:
        self._update_subscriber_flag(chat_id, "show_time", enabled)

    def set_show_link(self, chat_id: int, enabled: bool) -> None:
        self._update_subscriber_flag(chat_id, "show_link", enabled)

    def is_active_subscriber(self, chat_id: int) -> bool:
        subscriber = self.get_subscriber(chat_id)
        return bool(subscriber and subscriber.is_active)

    def list_active_subscribers(self) -> list[Subscriber]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id, chat_type, label, is_active, receive_card_posts, show_original, show_time, show_link
                FROM subscribers
                WHERE is_active = 1
                ORDER BY created_at ASC
                """
            ).fetchall()

        return [
            Subscriber(
                chat_id=int(row["chat_id"]),
                chat_type=str(row["chat_type"]),
                label=str(row["label"]),
                is_active=bool(row["is_active"]),
                receive_card_posts=bool(row["receive_card_posts"]),
                show_original=bool(row["show_original"]),
                show_time=bool(row["show_time"]),
                show_link=bool(row["show_link"]),
            )
            for row in rows
        ]

    def get_processed_news(self, guid: str) -> NewsInsight | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT guid, title, translated_title, explanation, source_url, published_at, is_breaking, image_url
                FROM processed_news
                WHERE guid = ?
                """,
                (guid,),
            ).fetchone()

        if row is None:
            return None

        return NewsInsight(
            guid=row["guid"],
            title=row["title"],
            translated_title=row["translated_title"],
            explanation=row["explanation"],
            link=row["source_url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            is_breaking=bool(row["is_breaking"]),
            image_url=row["image_url"],
        )

    def save_processed_news(self, insight: NewsInsight) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_news (
                    guid, title, translated_title, explanation, source_url, published_at, is_breaking, image_url, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guid) DO UPDATE SET
                    title = excluded.title,
                    translated_title = excluded.translated_title,
                    explanation = excluded.explanation,
                    source_url = excluded.source_url,
                    published_at = excluded.published_at,
                    is_breaking = excluded.is_breaking,
                    image_url = excluded.image_url
                """,
                (
                    insight.guid,
                    insight.title,
                    insight.translated_title,
                    insight.explanation,
                    insight.link,
                    insight.published_at.isoformat(),
                    int(insight.is_breaking),
                    insight.image_url,
                    self._now(),
                ),
            )

    def list_recent_processed_news(self, limit: int) -> list[NewsInsight]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT guid, title, translated_title, explanation, source_url, published_at, is_breaking, image_url
                FROM processed_news
                ORDER BY published_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            NewsInsight(
                guid=row["guid"],
                title=row["title"],
                translated_title=row["translated_title"],
                explanation=row["explanation"],
                link=row["source_url"],
                published_at=datetime.fromisoformat(row["published_at"]),
                is_breaking=bool(row["is_breaking"]),
                image_url=row["image_url"],
            )
            for row in rows
        ]

    def list_recent_processed_guids(self, limit: int) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT guid
                FROM processed_news
                ORDER BY published_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["guid"]) for row in rows]

    def has_sent_news(self, chat_id: int, guid: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sent_news WHERE chat_id = ? AND guid = ?",
                (chat_id, guid),
            ).fetchone()
        return row is not None

    def mark_news_sent(self, chat_id: int, guid: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO sent_news (guid, chat_id, sent_at)
                VALUES (?, ?, ?)
                """,
                (guid, chat_id, self._now()),
            )

    def seed_sent_news(self, chat_id: int, guids: list[str]) -> None:
        if not guids:
            return

        now = self._now()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO sent_news (guid, chat_id, sent_at)
                VALUES (?, ?, ?)
                """,
                [(guid, chat_id, now) for guid in guids],
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _update_subscriber_flag(self, chat_id: int, column: str, enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                f"UPDATE subscribers SET {column} = ?, updated_at = ? WHERE chat_id = ?",
                (int(enabled), self._now(), chat_id),
            )

    @staticmethod
    def _ensure_subscriber_columns(conn: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(subscribers)")}
        if "receive_card_posts" not in columns:
            conn.execute(
                "ALTER TABLE subscribers ADD COLUMN receive_card_posts INTEGER NOT NULL DEFAULT 0"
            )
        if "show_original" not in columns:
            conn.execute(
                "ALTER TABLE subscribers ADD COLUMN show_original INTEGER NOT NULL DEFAULT 0"
            )
        if "show_time" not in columns:
            conn.execute(
                "ALTER TABLE subscribers ADD COLUMN show_time INTEGER NOT NULL DEFAULT 1"
            )
        if "show_link" not in columns:
            conn.execute(
                "ALTER TABLE subscribers ADD COLUMN show_link INTEGER NOT NULL DEFAULT 0"
            )

    @staticmethod
    def _ensure_processed_news_columns(conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(processed_news)").fetchall()
        }
        if "is_breaking" not in columns:
            conn.execute(
                "ALTER TABLE processed_news ADD COLUMN is_breaking INTEGER NOT NULL DEFAULT 0"
            )
        if "image_url" not in columns:
            conn.execute("ALTER TABLE processed_news ADD COLUMN image_url TEXT")

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
