"""In-process publish/subscribe event bus (no sockets / WebSockets)."""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    TRANSCRIPT = "transcript"
    OCR_TEXT = "ocr_text"
    AI_TOKEN = "ai_token"
    AI_COMPLETE = "ai_complete"
    AI_ERROR = "ai_error"
    STATUS = "status"
    STEALTH_CHANGED = "stealth_changed"
    OPACITY_CHANGED = "opacity_changed"
    REGION_SET = "region_set"
    HOTKEY = "hotkey"
    DOCUMENT_INDEXED = "document_indexed"
    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"


@dataclass(slots=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


Subscriber = Callable[[Event], None]


class EventBus:
    """Thread-safe in-memory event bus for native slot-style fan-out."""

    def __init__(self) -> None:
        self._subs: dict[EventType, list[Subscriber]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, event_type: EventType, callback: Subscriber) -> None:
        with self._lock:
            if callback not in self._subs[event_type]:
                self._subs[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Subscriber) -> None:
        with self._lock:
            listeners = self._subs.get(event_type, [])
            if callback in listeners:
                listeners.remove(callback)

    def publish(self, event_type: EventType, **payload: Any) -> None:
        event = Event(type=event_type, payload=payload)
        with self._lock:
            listeners = list(self._subs.get(event_type, []))
        for cb in listeners:
            try:
                cb(event)
            except Exception:  # noqa: BLE001 — never let a subscriber kill the bus
                from src.core.logging_setup import get_logger

                get_logger("event_bus").exception("Subscriber failed for %s", event_type)


# Process singleton
BUS = EventBus()
