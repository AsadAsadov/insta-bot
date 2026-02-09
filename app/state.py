from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Deque


@dataclass
class EventBuffer:
    maxlen: int = 200
    _events: Deque[dict[str, Any]] = field(init=False)
    _lock: Lock = field(default_factory=Lock)

    def __post_init__(self) -> None:
        self._events = deque(maxlen=self.maxlen)

    def add(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        return events[-limit:]


event_buffer = EventBuffer()
