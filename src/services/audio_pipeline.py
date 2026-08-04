"""
Hardware-isolated dual audio pipeline (QThreads).

Thread A — Candidate: default MICROPHONE only → [CANDIDATE]
Thread B — Interviewer: WASAPI loopback only → [INTERVIEWER]

Each thread has independent VAD + Groq Whisper. Never mixes PCM channels.
Echo suppression drops mic transcripts that mirror recent loopback text
(speaker → mic bleed), which caused identical blue+grey bubbles.
"""

from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from src.core.config import CONFIG, AudioConfig
from src.core.logging_setup import get_logger
from src.utils.audio_devices import resolve_loopback_device, resolve_mic_device
from src.utils.text_similarity import text_similarity
from src.utils.vad import RingPCMBuffer, VoiceActivityDetector, pcm16_to_wav
from src.utils.wasapi_loopback import LoopbackHandle, open_wasapi_loopback, resample_mono_f32

log = get_logger("audio_pipeline")


def _utcnow_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


# Back-compat alias used by tests / callers
_similarity = text_similarity
_resample_mono_f32 = resample_mono_f32


class GroqWhisperClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._client: Any = None

    def _client_or_raise(self) -> Any:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is missing — set it in .env")
        if self._client is None:
            from groq import Groq

            self._client = Groq(api_key=self.api_key)
        return self._client

    def transcribe(self, wav_bytes: bytes, tag: str) -> str:
        client = self._client_or_raise()
        bio = io.BytesIO(wav_bytes)
        bio.name = f"{tag}.wav"
        t0 = time.perf_counter()
        try:
            result = client.audio.transcriptions.create(
                file=(f"{tag}.wav", bio, "audio/wav"),
                model=self.model,
                response_format="text",
                temperature=0.0,
                language="en",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[PIPELINE ERROR] Groq Whisper failed tag={tag} err={exc}", flush=True)
            raise
        text = result if isinstance(result, str) else getattr(result, "text", str(result))
        text = (text or "").strip()
        ms = (time.perf_counter() - t0) * 1000
        print(
            f"[GROQ TRANSCRIPT RECEIVED] tag={tag} ms={ms:.0f} text={text[:160]!r}",
            flush=True,
        )
        return text


class _CaptureWorker(QThread):
    """One isolated capture+VAD+Whisper worker bound to a single device role."""

    utterance_ready = pyqtSignal(str, bytes)  # tag, pcm
    capture_error = pyqtSignal(str)
    error_occurred = pyqtSignal(str)  # GUI-safe alias (QueuedConnection)
    capture_status = pyqtSignal(str)

    def __init__(
        self,
        *,
        tag: str,
        loopback: bool,
        device: int | None,
        config: AudioConfig,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.tag = tag  # [INTERVIEWER] or [CANDIDATE]
        self.loopback = loopback
        self.device = device
        self.config = config
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _emit_error(self, msg: str) -> None:
        print(f"[AUDIO CAPTURED] ERROR {msg}", flush=True)
        self.capture_error.emit(msg)
        self.error_occurred.emit(msg)

    def run(self) -> None:
        cfg = self.config
        role = "WASAPI_LOOPBACK" if self.loopback else "MICROPHONE"
        print(
            f"[AUDIO CAPTURED] START tag={self.tag} role={role} device={self.device} "
            f"sr={cfg.sample_rate} thread={threading.current_thread().name}",
            flush=True,
        )
        try:
            if self.loopback:
                self._run_loopback(cfg, role)
            else:
                self._run_microphone(cfg, role)
        except Exception as exc:  # noqa: BLE001
            # Last-resort safety net — never crash the GUI / orphan running=True
            msg = f"Capture worker crashed {self.tag}: {exc}"
            log.exception(msg)
            self._emit_error(msg)
        finally:
            print(f"[AUDIO CAPTURED] STOP tag={self.tag}", flush=True)

    def _process_mono_chunk(
        self,
        mono_f32: np.ndarray,
        *,
        vad: VoiceActivityDetector,
        ring: RingPCMBuffer,
        role: str,
        cfg: AudioConfig,
    ) -> None:
        clipped = np.clip(mono_f32.astype(np.float32, copy=False), -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16).tobytes()

        arr = np.frombuffer(pcm, dtype=np.int16)
        if arr.size:
            rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
            if rms < 180:
                pcm = b"\x00\x00" * arr.size

        result = vad.process(pcm)
        if result.is_speech or vad.in_utterance:
            ring.append(pcm)

        if vad.should_finalize() and len(ring) > 0:
            blob = ring.dump()
            min_bytes = int(cfg.sample_rate * (cfg.min_utterance_ms / 1000.0) * 2)
            vad.reset()
            if len(blob) < min_bytes:
                return
            a2 = np.frombuffer(blob, dtype=np.int16)
            rms2 = float(np.sqrt(np.mean(a2.astype(np.float32) ** 2))) if a2.size else 0.0
            if rms2 < 120:
                print(
                    f"[AUDIO CAPTURED] pruned silent chunk tag={self.tag} rms={rms2:.1f}",
                    flush=True,
                )
                return
            print(
                f"[AUDIO CAPTURED] utterance tag={self.tag} role={role} "
                f"bytes={len(blob)} rms={rms2:.1f}",
                flush=True,
            )
            self.utterance_ready.emit(self.tag, blob)

    def _flush_ring(self, vad: VoiceActivityDetector, ring: RingPCMBuffer) -> None:
        if vad.in_utterance and len(ring) > 0:
            blob = ring.dump()
            if blob:
                self.utterance_ready.emit(self.tag, blob)

    def _run_loopback(self, cfg: AudioConfig, role: str) -> None:
        """
        Interviewer path: open a real WASAPI loopback source.

        NEVER call sounddevice.WasapiSettings with a loopback keyword — invalid on 0.5.x.
        Speakers (in=0 out=2) cannot be opened as InputStream; use PyAudioWPatch /
        soundcard / Stereo Mix via open_wasapi_loopback().
        """
        handle: LoopbackHandle | None = None
        try:
            handle = open_wasapi_loopback(target_rate=cfg.sample_rate)
        except Exception as exc:  # noqa: BLE001
            msg = f"Failed to enable WASAPI loopback: {exc}"
            self._emit_error(msg)
            return

        self.capture_status.emit(f"{self.tag} capturing ({role} via {handle.name})")
        print(
            f"[AUDIO CAPTURED] loopback handle={handle.name} "
            f"native_sr={handle.sample_rate} ch={handle.channels}",
            flush=True,
        )

        vad = VoiceActivityDetector(
            sample_rate=cfg.sample_rate,
            frame_ms=cfg.chunk_ms,
            aggressiveness=cfg.vad_aggressiveness,
            silence_hangover_ms=cfg.silence_hangover_ms,
        )
        ring = RingPCMBuffer(cfg.max_utterance_ms, cfg.sample_rate)
        # Read at native rate, then resample to cfg.sample_rate for VAD/Whisper
        native_block = max(1, int(handle.sample_rate * cfg.chunk_ms / 1000))

        try:
            while not self._stop.is_set():
                try:
                    mono_native = handle.read(native_block)
                except Exception as exc:  # noqa: BLE001
                    msg = f"Loopback read failed {self.tag}: {exc}"
                    self._emit_error(msg)
                    break
                mono = resample_mono_f32(mono_native, handle.sample_rate, cfg.sample_rate)
                if mono.size == 0:
                    self._stop.wait(0.01)
                    continue
                self._process_mono_chunk(mono, vad=vad, ring=ring, role=role, cfg=cfg)
            self._flush_ring(vad, ring)
        finally:
            try:
                handle.close()
            except Exception:  # noqa: BLE001
                pass

    def _run_microphone(self, cfg: AudioConfig, role: str) -> None:
        """Candidate path: sounddevice InputStream on the microphone only."""
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            self._emit_error(f"sounddevice unavailable for {self.tag}: {exc}")
            return

        vad = VoiceActivityDetector(
            sample_rate=cfg.sample_rate,
            frame_ms=cfg.chunk_ms,
            aggressiveness=cfg.vad_aggressiveness,
            silence_hangover_ms=cfg.silence_hangover_ms,
        )
        ring = RingPCMBuffer(cfg.max_utterance_ms, cfg.sample_rate)
        blocksize = int(cfg.sample_rate * cfg.chunk_ms / 1000)
        self.capture_status.emit(f"{self.tag} capturing ({role})")

        def _callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:  # noqa: ARG001
            if self._stop.is_set():
                raise sd.CallbackStop()
            if status:
                log.debug("%s stream status: %s", self.tag, status)
            # Strict mono from channel 0 only — never mix channels
            mono = indata[:, 0] if indata.ndim > 1 else indata
            self._process_mono_chunk(mono, vad=vad, ring=ring, role=role, cfg=cfg)

        # Shared-mode WASAPI only — never pass loopback= into WasapiSettings
        extra = None
        if hasattr(sd, "WasapiSettings"):
            try:
                extra = sd.WasapiSettings(exclusive=False, auto_convert=True)
            except TypeError:
                try:
                    extra = sd.WasapiSettings(exclusive=False)
                except Exception:  # noqa: BLE001
                    extra = None

        try:
            with sd.InputStream(
                samplerate=cfg.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=blocksize,
                device=self.device,
                callback=_callback,
                extra_settings=extra,
            ):
                while not self._stop.is_set():
                    self._stop.wait(0.15)
            self._flush_ring(vad, ring)
        except Exception as exc:  # noqa: BLE001
            msg = f"Capture stream failed {self.tag}: {exc}"
            log.exception(msg)
            self._emit_error(msg)


class AudioPipeline(QObject):
    """
    Orchestrates two isolated QThreads and emits diarized transcripts.

    Signals (GUI-thread safe via QueuedConnection):
      interviewer_transcribed(str)
      candidate_transcribed(str)
      pipeline_error(str)
      pipeline_status(str)
    """

    interviewer_transcribed = pyqtSignal(str)
    candidate_transcribed = pyqtSignal(str)
    pipeline_error = pyqtSignal(str)
    pipeline_status = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = CONFIG.audio
        self.whisper = GroqWhisperClient(CONFIG.groq_api_key, CONFIG.ai.groq_model)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="whisper")
        self._mic_thread: _CaptureWorker | None = None
        self._loop_thread: _CaptureWorker | None = None
        self._running = False
        self._lock = threading.RLock()
        # Recent interviewer lines for echo suppression
        self._recent_interviewer: list[tuple[float, str]] = []
        self.echo_similarity_threshold = 0.82
        self.echo_window_sec = 12.0

    @property
    def running(self) -> bool:
        return self._running

    # ---- device discovery ----

    def _find_mic_device(self) -> int | None:
        """Return mic index, or None to use sounddevice's default input."""
        try:
            idx = resolve_mic_device(self.config.mic_device_index)
            print(f"[AUDIO CAPTURED] mic device resolved → {idx!r}", flush=True)
            return idx
        except Exception as exc:  # noqa: BLE001
            msg = f"Mic discovery failed: {exc} — falling back to system default"
            print(f"[PIPELINE ERROR] {msg}", flush=True)
            self.pipeline_error.emit(msg)
            return None

    def _find_loopback_device(self) -> int | None:
        try:
            idx = resolve_loopback_device(self.config.loopback_device_index)
            print(f"[AUDIO CAPTURED] loopback device resolved → {idx!r}", flush=True)
            return idx
        except Exception as exc:  # noqa: BLE001
            msg = f"Loopback discovery failed: {exc}"
            print(f"[PIPELINE ERROR] {msg}", flush=True)
            self.pipeline_error.emit(msg)
            return None

    def list_devices(self) -> list[dict[str, Any]]:
        try:
            import sounddevice as sd

            out = []
            for i, d in enumerate(sd.query_devices()):
                out.append(
                    {
                        "index": i,
                        "name": d["name"],
                        "max_input": d["max_input_channels"],
                        "max_output": d["max_output_channels"],
                        "hostapi": sd.query_hostapis(d["hostapi"])["name"],
                    }
                )
            return out
        except Exception:  # noqa: BLE001
            return []

    # ---- lifecycle ----

    def start(self) -> None:
        if self._running:
            return
        if not CONFIG.groq_api_key:
            msg = "GROQ_API_KEY missing — cannot start audio pipeline"
            print(f"[PIPELINE ERROR] {msg}", flush=True)
            self.pipeline_error.emit(msg)
            return

        # Dump device map once for field diagnostics
        self._dump_devices()

        mic = self._find_mic_device()
        # Speaker / Stereo Mix index is diagnostic only — true loopback is opened
        # inside the interviewer thread via open_wasapi_loopback() (PyAudioWPatch /
        # soundcard / Stereo Mix). Never open Speakers (in=0) as InputStream.
        loopback_hint = self._find_loopback_device()
        if loopback_hint is None:
            print(
                "[AUDIO CAPTURED] no sounddevice speaker/stereo-mix hint — "
                "interviewer thread will still try PyAudioWPatch/soundcard",
                flush=True,
            )

        # Candidate mic thread — NEVER loopback
        self._mic_thread = _CaptureWorker(
            tag="[CANDIDATE]",
            loopback=False,
            device=mic,  # None ⇒ system default mic (OK)
            config=self.config,
        )
        self._mic_thread.utterance_ready.connect(self._on_utterance)
        # Prefer error_occurred (GUI contract); capture_error kept for back-compat callers
        self._mic_thread.error_occurred.connect(self.pipeline_error.emit)
        self._mic_thread.capture_status.connect(self.pipeline_status.emit)
        self._mic_thread.finished.connect(self._on_worker_finished)

        # Interviewer WASAPI loopback — separate QThread; failures must not kill mic/GUI
        self._loop_thread = _CaptureWorker(
            tag="[INTERVIEWER]",
            loopback=True,
            device=loopback_hint,
            config=self.config,
        )
        self._loop_thread.utterance_ready.connect(self._on_utterance)
        self._loop_thread.error_occurred.connect(self.pipeline_error.emit)
        self._loop_thread.capture_status.connect(self.pipeline_status.emit)
        self._loop_thread.finished.connect(self._on_worker_finished)

        self._mic_thread.start()
        self._loop_thread.start()

        self._running = True
        self.pipeline_status.emit("Listening — mic + WASAPI loopback isolated")
        print(
            f"[AUDIO CAPTURED] pipeline started mic={mic!r} loopback_hint={loopback_hint!r}",
            flush=True,
        )

    def _on_worker_finished(self) -> None:
        """Clear running if both capture threads have died (no orphan running=True)."""
        mic_alive = self._mic_thread is not None and self._mic_thread.isRunning()
        loop_alive = self._loop_thread is not None and self._loop_thread.isRunning()
        if self._running and not mic_alive and not loop_alive:
            self._running = False
            msg = "Audio capture ended (both streams stopped)"
            print(f"[AUDIO CAPTURED] {msg}", flush=True)
            self.pipeline_status.emit(msg)

    def _dump_devices(self) -> None:
        try:
            import sounddevice as sd
            from src.utils.audio_devices import split_default_device

            default = sd.default.device
            inn, out = split_default_device(default)
            print(
                f"[AUDIO CAPTURED] sd.default.device type={type(default).__name__} "
                f"raw={default!r} input={inn!r} output={out!r}",
                flush=True,
            )
            for i, d in enumerate(sd.query_devices()):
                try:
                    host = sd.query_hostapis(d["hostapi"])["name"]
                except Exception:
                    host = "?"
                print(
                    f"[AUDIO CAPTURED] device[{i}] in={d['max_input_channels']} "
                    f"out={d['max_output_channels']} host={host} name={d['name']!r}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[PIPELINE ERROR] device dump failed: {exc}", flush=True)

    def stop(self) -> None:
        for t in (self._mic_thread, self._loop_thread):
            if t is not None:
                t.stop()
                t.wait(2000)
        self._mic_thread = None
        self._loop_thread = None
        self._running = False
        self.pipeline_status.emit("Audio stopped")
        print("[AUDIO CAPTURED] pipeline stopped", flush=True)

    # ---- transcription + diarization ----

    def _on_utterance(self, tag: str, pcm: bytes) -> None:
        self._pool.submit(self._transcribe_job, tag, pcm)

    def _is_echo_of_interviewer(self, text: str) -> bool:
        now = time.time()
        with self._lock:
            self._recent_interviewer = [
                (ts, t) for ts, t in self._recent_interviewer if now - ts <= self.echo_window_sec
            ]
            for _, prior in self._recent_interviewer:
                score = text_similarity(text, prior)
                if score >= self.echo_similarity_threshold:
                    print(
                        f"[DIARIZATION] ECHO SUPPRESSED candidate≈interviewer "
                        f"score={score:.2f} text={text[:80]!r}",
                        flush=True,
                    )
                    return True
        return False

    def _remember_interviewer(self, text: str) -> None:
        with self._lock:
            self._recent_interviewer.append((time.time(), text))
            if len(self._recent_interviewer) > 20:
                self._recent_interviewer = self._recent_interviewer[-20:]

    def _transcribe_job(self, tag: str, pcm: bytes) -> None:
        try:
            wav = pcm16_to_wav(pcm, self.config.sample_rate, 1)
            text = self.whisper.transcribe(wav, tag=tag.replace("[", "").replace("]", "").lower())
            if not text:
                print(f"[GROQ TRANSCRIPT RECEIVED] tag={tag} EMPTY — dropped", flush=True)
                return

            stamp = _utcnow_stamp()
            if tag == "[INTERVIEWER]":
                self._remember_interviewer(text)
                line = f"{stamp} - [INTERVIEWER]: {text}"
                print(f"[DIARIZATION] KEEP {line[:160]}", flush=True)
                self.interviewer_transcribed.emit(text)
            elif tag == "[CANDIDATE]":
                if self._is_echo_of_interviewer(text):
                    # Do not emit — prevents duplicate blue+grey bubbles
                    return
                line = f"{stamp} - [CANDIDATE]: {text}"
                print(f"[DIARIZATION] KEEP {line[:160]}", flush=True)
                self.candidate_transcribed.emit(text)
            else:
                print(f"[DIARIZATION] UNKNOWN tag={tag} text={text[:80]!r}", flush=True)
        except Exception as exc:  # noqa: BLE001
            msg = f"Transcription error ({tag}): {exc}"
            log.exception(msg)
            print(f"[PIPELINE ERROR] {msg}", flush=True)
            self.pipeline_error.emit(msg)
