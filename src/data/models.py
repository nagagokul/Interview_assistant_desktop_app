"""Typed data models for sessions, messages, settings, and documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SessionRecord:
    id: str
    title: str
    started_at: datetime
    ended_at: datetime | None = None
    company: str = ""
    role: str = ""
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageRecord:
    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    speaker: str = ""
    source: str = ""  # audio | ocr | manual | ai
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentRecord:
    id: str
    filename: str
    path: str
    doc_type: str  # resume | jd | pdf | notes
    chunk_count: int
    indexed_at: datetime
    checksum: str = ""


@dataclass
class SettingRecord:
    key: str
    value: str
    updated_at: datetime
