from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Deque


@dataclass
class EventStore:
    maxlen: int = 200
    _events: Deque[dict[str, Any]] = field(init=False)
    _lock: Lock = field(default_factory=Lock)
    _last_payload: dict[str, Any] | None = None
    _drafts: dict[str, str] = field(default_factory=dict)
    _threads: dict[str, dict[str, Any]] = field(default_factory=dict)
    _message_index: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._events = deque(maxlen=self.maxlen)

    def add_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)
            thread_id = event.get("thread_id")
            if thread_id:
                self._threads[thread_id] = self._build_thread_summary(
                    thread_id, event
                )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        return events[-limit:]

    def list_threads(self) -> list[dict[str, Any]]:
        with self._lock:
            threads = list(self._threads.values())
        return sorted(
            threads,
            key=lambda thread: thread.get("updated_at") or "",
            reverse=True,
        )

    def get_draft(self, thread_id: str) -> str:
        with self._lock:
            return self._drafts.get(thread_id, "")

    def set_draft(self, thread_id: str, draft: str) -> None:
        with self._lock:
            self._drafts[thread_id] = draft

    def clear_draft(self, thread_id: str) -> None:
        with self._lock:
            self._drafts.pop(thread_id, None)

    def set_last_payload(self, payload: dict[str, Any] | None) -> None:
        with self._lock:
            self._last_payload = payload

    def get_last_payload(self) -> dict[str, Any] | None:
        with self._lock:
            return self._last_payload

    def register_message(self, message_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            self._message_index[message_id] = event

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._message_index.get(message_id)

    def _build_thread_summary(
        self, thread_id: str, event: dict[str, Any]
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        previous = self._threads.get(thread_id, {})
        last_preview = event.get("preview") or event.get("text") or ""
        return {
            "thread_id": thread_id,
            "last_preview": last_preview,
            "last_event_type": event.get("event_type"),
            "last_message_id": event.get("message_id")
            or previous.get("last_message_id"),
            "last_comment_id": event.get("comment_id")
            or previous.get("last_comment_id"),
            "updated_at": now,
        }


event_store = EventStore()
