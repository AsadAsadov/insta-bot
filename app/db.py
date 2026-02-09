from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

DB_PATH = os.getenv("DB_PATH", "app.db")


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                last_message TEXT,
                last_ts INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT,
                event_type TEXT,
                message_id TEXT,
                text TEXT,
                from_id TEXT,
                ts INTEGER,
                received_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                trigger_type TEXT,
                trigger_value TEXT,
                reply_text TEXT,
                is_active INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT,
                text TEXT,
                status TEXT,
                error TEXT,
                created_at TEXT,
                sent_at TEXT
            )
            """
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_thread(thread_id: str, last_message: str, last_ts: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO threads (id, last_message, last_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_message=excluded.last_message,
                last_ts=excluded.last_ts
            """,
            (thread_id, last_message, last_ts),
        )


def insert_event(
    thread_id: str,
    event_type: str,
    message_id: str | None,
    text: str | None,
    from_id: str | None,
    ts: int | None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO events (
                thread_id, event_type, message_id, text, from_id, ts, received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                event_type,
                message_id,
                text,
                from_id,
                ts,
                utc_now_iso(),
            ),
        )


def list_threads() -> list[sqlite3.Row]:
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id, last_message, last_ts FROM threads ORDER BY last_ts DESC"
        )
        return cursor.fetchall()


def get_thread_events(thread_id: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, thread_id, event_type, message_id, text, from_id, ts, received_at
            FROM events
            WHERE thread_id = ?
            ORDER BY ts ASC, id ASC
            """,
            (thread_id,),
        )
        return cursor.fetchall()


def create_outbox(thread_id: str, text: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO outbox (thread_id, text, status, error, created_at, sent_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (thread_id, text, "pending", None, utc_now_iso(), None),
        )
        return int(cursor.lastrowid)


def update_outbox(
    outbox_id: int, status: str, error: str | None, sent_at: str | None
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE outbox
            SET status = ?, error = ?, sent_at = ?
            WHERE id = ?
            """,
            (status, error, sent_at, outbox_id),
        )


def list_outbox(thread_id: str, limit: int = 5) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, thread_id, text, status, error, created_at, sent_at
            FROM outbox
            WHERE thread_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (thread_id, limit),
        )
        return cursor.fetchall()


def get_latest_failed_outbox(thread_id: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, thread_id, text, status, error, created_at, sent_at
            FROM outbox
            WHERE thread_id = ? AND status = 'failed'
            ORDER BY id DESC
            LIMIT 1
            """,
            (thread_id,),
        )
        return cursor.fetchone()


def list_templates() -> list[sqlite3.Row]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, name, trigger_type, trigger_value, reply_text, is_active
            FROM templates
            ORDER BY id ASC
            """
        )
        return cursor.fetchall()


def create_template(
    name: str,
    trigger_type: str,
    trigger_value: str,
    reply_text: str,
    is_active: int,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO templates (name, trigger_type, trigger_value, reply_text, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, trigger_type, trigger_value, reply_text, is_active),
        )


def toggle_template(template_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE templates
            SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
            WHERE id = ?
            """,
            (template_id,),
        )


def delete_template(template_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))


def find_matching_template(text: str | None) -> sqlite3.Row | None:
    templates = list_templates()
    for template in templates:
        if not template["is_active"]:
            continue
        trigger_type = template["trigger_type"]
        trigger_value = (template["trigger_value"] or "").strip()
        if trigger_type == "any":
            return template
        if text is None:
            continue
        if trigger_type == "equals" and text == trigger_value:
            return template
        if trigger_type == "contains" and trigger_value and trigger_value in text:
            return template
        if trigger_type == "regex" and trigger_value:
            try:
                import re

                if re.search(trigger_value, text):
                    return template
            except re.error:
                continue
    return None


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


__all__ = [
    "init_db",
    "get_connection",
    "upsert_thread",
    "insert_event",
    "list_threads",
    "get_thread_events",
    "create_outbox",
    "update_outbox",
    "list_outbox",
    "get_latest_failed_outbox",
    "list_templates",
    "create_template",
    "toggle_template",
    "delete_template",
    "find_matching_template",
    "row_to_dict",
]
