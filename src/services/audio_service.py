"""
AudioCaptureService — dual-stream diarization capture.

- Interviewer: WASAPI loopback (system speakers / headphones)
- Candidate: microphone via PyAudio / sounddevice

Chunks are VAD-segmented and transcribed via Groq whisper-large-v3.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import numpy as np

from src.core.config import CONFIG, AudioConfig
from src.core.context import CTX
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger
from src.utils.vad import RingPCMBuffer, VoiceActivityDetector, pcm16_to_wav

log = get_logger("audio")


class GroqTranscriber:
    """Thin Groq Whisper client (HTTP) for low-latency chunk transcription."""

    def __init__(self, api_key: str, model: str = "whisper-large-v3") -> None:
        self.api_key = api_key
        self.model = model
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from groq import Groq

            self._client = Groq(api_key=self.api_key)
        return self._client

    def transcribe(self, wav_bytes: bytes, language: str | None = "en") -> str:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is not configured")
        client = self._ensure_client()
        bio = io.BytesIO(wav_bytes)
        bio.name = "utterance.wav"
        t0 = time.perf_counter()
        kwargs: dict[str, Any] = {
            "file": ("utterance.wav", bio, "audio/wav"),
            "model": self.model,
            "response_format": "text",
            "temperature": 0.0,
        }
        if language:
            kwargs["language"] = language
        result = client.audio.transcriptions.create(**kwargs)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        text = result if isinstance(result, str) else getattr(result, "text", str(result))
        text = (text or "").strip()
        log.info("Groq Whisper %.0fms → %d chars", elapsed_ms, len(text))
        return text


class _StreamWorker(threading.Thread):
    """Capture one audio device, run VAD, emit utterances."""

    def __init__(
        self,
        name: str,
        speaker: str,
        device: int | None,
        loopback: bool,
        config: AudioConfig,
        on_utterance: Callable[[str, bytes], None],
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name=name, daemon=True)
        self.speaker = speaker
        self.device = device
        self.loopback = loopback
        self.config = config
        self.on_utterance = on_utterance
        self.stop_event = stop_event
        self._sd = None

    def run(self) -> None:
        try:
            import sounddevice as sd

            self._sd = sd
        except Exception:  # noqa: BLE001
            log.exception("[%s] sounddevice not available", self.speaker)
            BUS.publish(EventType.STATUS, message=f"Audio backend missing ({self.speaker})")
            return

        cfg = self.config
        vad = VoiceActivityDetector(
            sample_rate=cfg.sample_rate,
            frame_ms=cfg.chunk_ms,
            aggressiveness=cfg.vad_aggressiveness,
            silence_hangover_ms=cfg.silence_hangover_ms,
        )
        ring = RingPCMBuffer(cfg.max_utterance_ms, cfg.sample_rate)
        blocksize = int(cfg.sample_rate * cfg.chunk_ms / 1000)

        def _callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:  # noqa: ARG001
            if status:
                log.debug("[%s] stream status: %s", self.speaker, status)
            if self.stop_event.is_set():
                raise sd.CallbackStop()
            # Convert float32 [-1,1] -> PCM16
            clipped = np.clip(indata[:, 0], -1.0, 1.0)
            pcm = (clipped * 32767.0).astype(np.int16).tobytes()
            if cfg.noise_suppress:
                pcm = self._soft_noise_gate(pcm)

            result = vad.process(pcm)
            if result.is_speech or vad.in_utterance:
                ring.append(pcm)
            if vad.should_finalize() and len(ring) > 0:
                blob = ring.dump()
                min_bytes = int(cfg.sample_rate * (cfg.min_utterance_ms / 1000.0) * 2)
                vad.reset()
                if len(blob) >= min_bytes:
                    self.on_utterance(self.speaker, blob)

        extra = None
        try:
            # WASAPI loopback on Windows
            if self.loopback and hasattr(sd, "WasapiSettings"):
                extra = sd.WasapiSettings(loopback=True)
        except Exception:  # noqa: BLE001
            extra = None

        try:
            log.info(
                "[%s] opening stream device=%s loopback=%s sr=%s",
                self.speaker,
                self.device,
                self.loopback,
                cfg.sample_rate,
            )
            with sd.InputStream(
                samplerate=cfg.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=blocksize,
                device=self.device,
                callback=_callback,
                extra_settings=extra,
            ):
                while not self.stop_event.is_set():
                    self.stop_event.wait(0.2)
                    # Flush hanging utterance on stop
            if vad.in_utterance and len(ring) > 0:
                blob = ring.dump()
                if blob:
                    self.on_utterance(self.speaker, blob)
        except Exception:  # noqa: BLE001
            log.exception("[%s] capture stream failed", self.speaker)
            BUS.publish(
                EventType.STATUS,
                message=f"Audio capture error ({self.speaker}) — check device permissions",
            )

    @staticmethod
    def _soft_noise_gate(pcm: bytes, threshold: int = 180) -> bytes:
        """Simple soft gate — zeros near-silent frames to cut fan noise."""
        arr = np.frombuffer(pcm, dtype=np.int16).copy()
        if arr.size == 0:
            return pcm
        rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
        if rms < threshold:
            arr[:] = 0
        return arr.tobytes()


class AudioCaptureService:
    """
    Orchestrates parallel interviewer (loopback) + candidate (mic) capture
    and async Groq transcription with automatic punctuation (Whisper default).
    """

    def __init__(self) -> None:
        self.config = CONFIG.audio
        self.transcriber = GroqTranscriber(CONFIG.groq_api_key, CONFIG.ai.groq_model)
        self._stop = threading.Event()
        self._workers: list[_StreamWorker] = []
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="whisper")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def list_devices(self) -> list[dict[str, Any]]:
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            result = []
            for i, d in enumerate(devices):
                result.append(
                    {
                        "index": i,
                        "name": d["name"],
                        "max_input": d["max_input_channels"],
                        "max_output": d["max_output_channels"],
                        "default_sr": d["default_samplerate"],
                        "hostapi": sd.query_hostapis(d["hostapi"])["name"],
                    }
                )
            return result
        except Exception:  # noqa: BLE001
            log.exception("Device enumeration failed")
            return []

    def _find_loopback_device(self) -> int | None:
        if self.config.loopback_device_index is not None:
            return self.config.loopback_device_index
        try:
            import sounddevice as sd

            # Prefer default output mapped as WASAPI loopback input
            for i, d in enumerate(sd.query_devices()):
                name = str(d["name"]).lower()
                host = sd.query_hostapis(d["hostapi"])["name"].lower()
                if "wasapi" in host and d["max_input_channels"] > 0:
                    if "loopback" in name or "stereo mix" in name or "what u hear" in name:
                        return i
            # Fallback: default output device index (WasapiSettings.loopback=True)
            default = sd.default.device
            if isinstance(default, (list, tuple)) and len(default) >= 2:
                return int(default[1])  # output device for loopback
            return None
        except Exception:  # noqa: BLE001
            log.exception("Loopback device discovery failed")
            return None

    def _find_mic_device(self) -> int | None:
        if self.config.mic_device_index is not None:
            return self.config.mic_device_index
        try:
            import sounddevice as sd

            default = sd.default.device
            if isinstance(default, (list, tuple)):
                return int(default[0])
            return int(default)
        except Exception:  # noqa: BLE001
            return None

    def start(self) -> None:
        if self._running:
            return
        if not CONFIG.groq_api_key:
            BUS.publish(EventType.STATUS, message="Set GROQ_API_KEY in .env to enable transcription")
            log.error("Cannot start audio — missing GROQ_API_KEY")
            return

        self._stop.clear()
        mic = self._find_mic_device()
        loopback = self._find_loopback_device()

        workers = [
            _StreamWorker(
                name="MicCapture",
                speaker="candidate",
                device=mic,
                loopback=False,
                config=self.config,
                on_utterance=self._on_utterance,
                stop_event=self._stop,
            ),
            _StreamWorker(
                name="LoopbackCapture",
                speaker="interviewer",
                device=loopback,
                loopback=True,
                config=self.config,
                on_utterance=self._on_utterance,
                stop_event=self._stop,
            ),
        ]
        self._workers = workers
        for w in workers:
            w.start()
        self._running = True
        CTX.is_listening = True
        CTX.set_status("Listening (mic + system audio)")
        BUS.publish(EventType.STATUS, message="Audio capture started")
        log.info("AudioCaptureService started mic=%s loopback=%s", mic, loopback)

    def stop(self) -> None:
        self._stop.set()
        for w in self._workers:
            w.join(timeout=2.0)
        self._workers.clear()
        self._running = False
        CTX.is_listening = False
        CTX.set_status("Audio stopped")
        BUS.publish(EventType.STATUS, message="Audio capture stopped")
        log.info("AudioCaptureService stopped")

    def _on_utterance(self, speaker: str, pcm: bytes) -> None:
        self._pool.submit(self._transcribe_job, speaker, pcm)

    def _transcribe_job(self, speaker: str, pcm: bytes) -> None:
        try:
            wav = pcm16_to_wav(pcm, self.config.sample_rate, 1)
            text = self.transcriber.transcribe(wav)
            if not text:
                return
            CTX.add_transcript(speaker, text)
            BUS.publish(
                EventType.TRANSCRIPT,
                speaker=speaker,
                text=text,
            )
            log.debug("Transcript [%s]: %s", speaker, text[:120])
        except Exception as exc:  # noqa: BLE001
            log.exception("Transcription failed")
            BUS.publish(EventType.AI_ERROR, message=f"Transcription error: {exc}")
