"""
Compatibility facade: AudioCaptureService → AudioPipeline.

Prefer AudioPipeline directly. This wrapper keeps older call sites working
(start/stop/running/list_devices/rolling_context).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.context import CTX
from src.core.logging_setup import get_logger
from src.services.audio_pipeline import AudioPipeline

if TYPE_CHECKING:
    from src.core.stream_hub import StreamHub
    from src.services.ai_orchestrator import AIOrchestrator

log = get_logger("audio")


class AudioCaptureService:
    """Thin wrapper that owns an AudioPipeline and mirrors signals into StreamHub + AI memory."""

    def __init__(
        self,
        hub: StreamHub | None = None,
        ai: AIOrchestrator | None = None,
    ) -> None:
        self.hub = hub
        self.ai = ai
        self.pipeline = AudioPipeline()
        self.rolling_context: str = ""
        self._wired = False
        self._wire_pipeline()

    def set_hub(self, hub: StreamHub) -> None:
        self.hub = hub

    def set_ai(self, ai: AIOrchestrator) -> None:
        self.ai = ai

    def _wire_pipeline(self) -> None:
        if self._wired:
            return
        self.pipeline.interviewer_transcribed.connect(self._on_interviewer)
        self.pipeline.candidate_transcribed.connect(self._on_candidate)
        self.pipeline.pipeline_error.connect(self._on_error)
        self.pipeline.pipeline_status.connect(self._on_status)
        self._wired = True

    @property
    def running(self) -> bool:
        return self.pipeline.running

    def list_devices(self) -> list[dict[str, Any]]:
        return self.pipeline.list_devices()

    def start(self) -> None:
        self.pipeline.start()
        CTX.is_listening = self.pipeline.running

    def stop(self) -> None:
        self.pipeline.stop()
        CTX.is_listening = False

    def _on_interviewer(self, text: str) -> None:
        line = f"[INTERVIEWER] {text}"
        self.rolling_context = (self.rolling_context + "\n" + line).strip()[-12000:]
        if self.ai is not None:
            self.ai.record_interviewer(text, auto_ask=True)
        else:
            CTX.add_transcript("interviewer", text)
        if self.hub is not None:
            self.hub.emit_transcript("interviewer", text)

    def _on_candidate(self, text: str) -> None:
        line = f"[CANDIDATE] {text}"
        self.rolling_context = (self.rolling_context + "\n" + line).strip()[-12000:]
        if self.ai is not None:
            self.ai.record_candidate(text)
        else:
            CTX.add_transcript("candidate", text)
        if self.hub is not None:
            self.hub.emit_transcript("candidate", text)

    def _on_error(self, message: str) -> None:
        log.error(message)
        print(f"[PIPELINE ERROR] {message}", flush=True)
        if self.hub is not None:
            # Do NOT send to ai_error (that paints the AI guidance panel).
            # Status + UI log strip via status signal is enough.
            self.hub.emit_status(message)

    def _on_status(self, message: str) -> None:
        CTX.set_status(message)
        if self.hub is not None:
            self.hub.emit_status(message)
