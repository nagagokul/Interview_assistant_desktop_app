"""High-speed shared application context (native memory slots)."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TranscriptLine:
    speaker: str  # "interviewer" | "candidate"
    text: str
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 1.0


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant" | "system" | "interviewer" | "candidate"
    content: str
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    meta: dict[str, Any] = field(default_factory=dict)


class AppContext:
    """
    Thread-safe shared state between AudioCapture, OCR, AI, and UI layers.

    Designed for near-zero-copy handoff via deques and locks — no IPC ports.
    """

    def __init__(self, history_limit: int = 200) -> None:
        self._lock = threading.RLock()
        self._history_limit = history_limit

        self.session_id: str | None = None
        self.stealth_enabled: bool = False
        self.opacity: float = 0.92
        self.overlay_visible: bool = True

        self.ocr_region: tuple[int, int, int, int] | None = None  # left, top, right, bottom
        self.latest_ocr_text: str = ""
        self.latest_ocr_image: bytes | None = None  # JPEG/PNG bytes for vision

        self.transcripts: deque[TranscriptLine] = deque(maxlen=history_limit)
        self.chat: deque[ChatMessage] = deque(maxlen=history_limit)
        self.resume_summary: str = ""
        self.job_description: str = ""
        self.rag_context: str = ""

        self.is_listening: bool = False
        self.is_ocr_running: bool = False
        self.is_ai_streaming: bool = False
        self.status_message: str = "Ready"

    # ---- mutators ----

    def set_session(self, session_id: str | None) -> None:
        with self._lock:
            self.session_id = session_id

    def add_transcript(self, speaker: str, text: str, confidence: float = 1.0) -> TranscriptLine:
        line = TranscriptLine(speaker=speaker, text=text.strip(), confidence=confidence)
        with self._lock:
            if line.text:
                self.transcripts.append(line)
        return line

    def add_chat(self, role: str, content: str, **meta: Any) -> ChatMessage:
        msg = ChatMessage(role=role, content=content, meta=meta)
        with self._lock:
            self.chat.append(msg)
        return msg

    def append_assistant_token(self, token: str) -> None:
        with self._lock:
            if self.chat and self.chat[-1].role == "assistant" and self.chat[-1].meta.get("streaming"):
                self.chat[-1].content += token
            else:
                self.chat.append(ChatMessage(role="assistant", content=token, meta={"streaming": True}))

    def finalize_assistant(self) -> None:
        with self._lock:
            if self.chat and self.chat[-1].role == "assistant":
                self.chat[-1].meta["streaming"] = False

    def set_ocr(self, text: str, image_bytes: bytes | None = None) -> None:
        with self._lock:
            self.latest_ocr_text = text
            if image_bytes is not None:
                self.latest_ocr_image = image_bytes

    def set_region(self, region: tuple[int, int, int, int] | None) -> None:
        with self._lock:
            self.ocr_region = region

    def set_status(self, message: str) -> None:
        with self._lock:
            self.status_message = message

    def recent_transcript(self, limit: int = 30) -> list[TranscriptLine]:
        with self._lock:
            items = list(self.transcripts)[-limit:]
        return items

    def transcript_block(self, limit: int = 30) -> str:
        lines = self.recent_transcript(limit)
        return "\n".join(f"[{t.speaker}] {t.text}" for t in lines)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "stealth": self.stealth_enabled,
                "opacity": self.opacity,
                "visible": self.overlay_visible,
                "ocr_region": self.ocr_region,
                "ocr_text": self.latest_ocr_text,
                "resume_summary": self.resume_summary,
                "job_description": self.job_description,
                "rag_context": self.rag_context,
                "listening": self.is_listening,
                "ocr_running": self.is_ocr_running,
                "ai_streaming": self.is_ai_streaming,
                "status": self.status_message,
                "transcript_count": len(self.transcripts),
                "chat_count": len(self.chat),
            }


# Process singleton
CTX = AppContext()
