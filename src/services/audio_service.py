"""
Audio capture facade → AudioPipeline.

WASAPI loopback notes (Windows 10/11)
-------------------------------------
sounddevice 0.5.x ``WasapiSettings`` does **not** accept ``loopback=True``.
Passing that keyword crashed Listen with:

    WasapiSettings.__init__() got an unexpected keyword argument 'loopback'

Real system-audio capture is implemented in ``src.utils.wasapi_loopback``:

  1. PyAudioWPatch — WASAPI loopback devices as true inputs
  2. soundcard     — include_loopback=True on the default speaker
  3. Stereo Mix    — normal sounddevice InputStream + WasapiSettings(exclusive=False)

Interviewer + candidate streams run on isolated QThreads. Stream init failures
emit ``error_occurred`` / ``pipeline_error`` back to the GUI and never leave
``running=True`` with a dead capture loop.
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
        print("[AUDIO CAPTURED] AudioCaptureService.start()", flush=True)
        try:
            self.pipeline.start()
        except Exception as exc:  # noqa: BLE001
            # Belt-and-suspenders: pipeline.start() already catches internally,
            # but never let Listen take down the Qt event loop.
            msg = f"Failed to start audio pipeline: {exc}"
            log.exception(msg)
            print(f"[PIPELINE ERROR] {msg}", flush=True)
            self._on_error(msg)
            CTX.is_listening = False
            return
        CTX.is_listening = self.pipeline.running
        print(f"[AUDIO CAPTURED] running={self.pipeline.running}", flush=True)

    def stop(self) -> None:
        print("[AUDIO CAPTURED] AudioCaptureService.stop()", flush=True)
        try:
            self.pipeline.stop()
        except Exception as exc:  # noqa: BLE001
            msg = f"Failed to stop audio pipeline: {exc}"
            log.exception(msg)
            self._on_error(msg)
        finally:
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
            self.hub.emit_status(message)

    def _on_status(self, message: str) -> None:
        CTX.set_status(message)
        if self.hub is not None:
            self.hub.emit_status(message)
