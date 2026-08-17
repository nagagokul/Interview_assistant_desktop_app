"""
Thread-safe PyQt6 signal hub for audio / AI / OCR → GUI routing.

MUST be constructed after QApplication exists. Worker threads call emit();
slots on the GUI thread receive via Qt.ConnectionType.QueuedConnection.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class StreamHub(QObject):
    """Central fan-in for all real-time text streams."""

    # Bi-directional dialogue
    interviewer_text = pyqtSignal(str)  # WASAPI / system loopback
    candidate_text = pyqtSignal(str)  # Microphone

    # AI guidance (separate viewport — never overwrites transcript)
    ai_chunk = pyqtSignal(str)
    ai_complete = pyqtSignal(str, float)  # full text, latency_ms
    ai_error = pyqtSignal(str)
    ai_started = pyqtSignal()

    # Ancillary
    ocr_text = pyqtSignal(str)
    status = pyqtSignal(str)
    # provider, message, warn, critical, rpm_used, rpm_limit, daily_used, daily_limit
    api_usage = pyqtSignal(str, str, bool, bool, int, int, int, int)

    def emit_api_usage(
        self,
        provider: str,
        message: str,
        *,
        warn: bool,
        critical: bool,
        rpm_used: int,
        rpm_limit: int,
        daily_used: int,
        daily_limit: int,
    ) -> None:
        self.api_usage.emit(
            provider,
            message,
            warn,
            critical,
            rpm_used,
            rpm_limit,
            daily_used,
            daily_limit,
        )

    def emit_transcript(self, speaker: str, text: str) -> None:
        speaker = (speaker or "").strip().lower()
        text = (text or "").strip()
        if not text:
            return
        if speaker == "interviewer":
            print(f"[UI ROUTE] interviewer_signal.emit({text[:80]!r})", flush=True)
            self.interviewer_text.emit(text)
        else:
            print(f"[UI ROUTE] candidate_signal.emit({text[:80]!r})", flush=True)
            self.candidate_text.emit(text)

    def emit_ai_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self.ai_chunk.emit(chunk)

    def emit_status(self, message: str) -> None:
        self.status.emit(message)
