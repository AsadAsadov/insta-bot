from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

logger = logging.getLogger("insta-bot")
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
        logger.exception("db_write_fail")
        raise
    finally:
        conn.close()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, last_message TEXT, last_ts INTEGER)")
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS comment_triggers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT,
              trigger_type TEXT,
              trigger_value TEXT,
              public_reply_text TEXT,
              dm_reply_text TEXT,
              is_active INTEGER DEFAULT 1
            )
            """
        )
    logger.info("db_write_success event=init_db")


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def upsert_thread(thread_id: str, last_message: str, last_ts: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO threads (id, last_message, last_ts) VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET last_message=excluded.last_message, last_ts=excluded.last_ts""",
            (thread_id, last_message, last_ts),
        )
    logger.info("db_write_success event=upsert_thread thread_id=%s", thread_id)


def insert_event(thread_id: str, event_type: str, message_id: str | None, text: str | None, from_id: str | None, ts: int | None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO events (thread_id, event_type, message_id, text, from_id, ts, received_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (thread_id, event_type, message_id, text, from_id, ts, utc_now_iso()),
        )
    logger.info("db_write_success event=insert_event thread_id=%s event_type=%s", thread_id, event_type)


def create_outbox(thread_id: str, text: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO outbox (thread_id, text, status, error, created_at, sent_at) VALUES (?, ?, 'pending', NULL, ?, NULL)",
            (thread_id, text, utc_now_iso()),
        )
        outbox_id = int(cur.lastrowid)
    logger.info("db_write_success event=create_outbox thread_id=%s outbox_id=%s", thread_id, outbox_id)
    return outbox_id


def update_outbox(outbox_id: int, status: str, error: str | None, sent_at: str | None) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE outbox SET status=?, error=?, sent_at=? WHERE id=?", (status, error, sent_at, outbox_id))
    logger.info("db_write_success event=update_outbox outbox_id=%s status=%s", outbox_id, status)


def list_threads() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT id, last_message, last_ts FROM threads ORDER BY COALESCE(last_ts, 0) DESC").fetchall()


def get_thread_events(thread_id: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, thread_id, event_type, message_id, text, from_id, ts, received_at FROM events WHERE thread_id=? ORDER BY COALESCE(ts,0) ASC, id ASC",
            (thread_id,),
        ).fetchall()


def get_latest_outbox_for_thread(thread_id: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, thread_id, text, status, error, created_at, sent_at FROM outbox WHERE thread_id=? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()


def create_template(name: str, trigger_type: str, trigger_value: str, reply_text: str, is_active: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO templates (name, trigger_type, trigger_value, reply_text, is_active) VALUES (?, ?, ?, ?, ?)",
            (name.strip(), trigger_type, (trigger_value or "").strip(), reply_text.strip(), int(bool(is_active))),
        )


def list_templates() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT id, name, trigger_type, trigger_value, reply_text, is_active FROM templates ORDER BY id ASC").fetchall()


def list_active_templates() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, name, trigger_type, trigger_value, reply_text, is_active FROM templates WHERE is_active=1 ORDER BY id ASC"
        ).fetchall()


def toggle_template(template_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE templates SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?", (template_id,))


def delete_template(template_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM templates WHERE id=?", (template_id,))


def find_first_matching_template(text: str) -> sqlite3.Row | None:
    normalized = (text or "").strip()
    if not normalized:
        return None
    for tpl in list_active_templates():
        ttype = (tpl["trigger_type"] or "").strip().lower()
        value = (tpl["trigger_value"] or "").strip()
        if ttype == "any":
            return tpl
        if ttype == "equals" and normalized.casefold() == value.casefold():
            return tpl
        if ttype == "contains" and value.casefold() in normalized.casefold():
            return tpl
        if ttype == "regex":
            try:
                if re.search(value, normalized, flags=re.IGNORECASE):
                    return tpl
            except re.error:
                continue
    return None


def create_comment_trigger(name: str, trigger_type: str, trigger_value: str, public_reply_text: str, dm_reply_text: str, is_active: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO comment_triggers (name, trigger_type, trigger_value, public_reply_text, dm_reply_text, is_active) VALUES (?, ?, ?, ?, ?, ?)",
            (name, trigger_type, trigger_value, public_reply_text, dm_reply_text, int(bool(is_active))),
        )


def list_active_comment_triggers() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, name, trigger_type, trigger_value, public_reply_text, dm_reply_text, is_active FROM comment_triggers WHERE is_active=1 ORDER BY id ASC"
        ).fetchall()


def find_first_matching_comment_trigger(text: str) -> sqlite3.Row | None:
    normalized = (text or "").strip()
    if not normalized:
        return None
    for trig in list_active_comment_triggers():
        ttype = (trig["trigger_type"] or "").strip().lower()
        value = (trig["trigger_value"] or "").strip()
        if ttype == "any":
            return trig
        if ttype == "equals" and normalized.casefold() == value.casefold():
            return trig
        if ttype == "contains" and value.casefold() in normalized.casefold():
            return trig
        if ttype == "regex":
            try:
                if re.search(value, normalized, flags=re.IGNORECASE):
                    return trig
            except re.error:
                continue
    return None
