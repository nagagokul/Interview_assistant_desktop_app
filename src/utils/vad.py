"""Voice Activity Detection helpers (WebRTC VAD + energy fallback)."""

from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass

import numpy as np

from src.core.logging_setup import get_logger

log = get_logger("vad")


@dataclass
class VADResult:
    is_speech: bool
    energy: float
    voiced_ms: int
    silence_ms: int


class VoiceActivityDetector:
    """
    Frame-level VAD with hangover for utterance segmentation.

    Prefers webrtcvad when installed; falls back to RMS energy gate so the
    app still works on constrained machines without the native extension.
    """

    def __init__(
        self,
        sample_rate: int = 16_000,
        frame_ms: int = 30,
        aggressiveness: int = 2,
        silence_hangover_ms: int = 600,
        energy_threshold: float = 350.0,
    ) -> None:
        if frame_ms not in (10, 20, 30):
            raise ValueError("frame_ms must be 10, 20, or 30")
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_bytes = int(sample_rate * (frame_ms / 1000.0) * 2)  # 16-bit mono
        self.silence_hangover_ms = silence_hangover_ms
        self.energy_threshold = energy_threshold
        self._voiced_ms = 0
        self._silence_ms = 0
        self._in_utterance = False
        self._webrtc = None
        try:
            import webrtcvad  # type: ignore

            self._webrtc = webrtcvad.Vad(aggressiveness)
            log.info("Using webrtcvad aggressiveness=%s", aggressiveness)
        except Exception:  # noqa: BLE001
            log.warning("webrtcvad unavailable — using energy-based VAD")

    def reset(self) -> None:
        self._voiced_ms = 0
        self._silence_ms = 0
        self._in_utterance = False

    def frame_energy(self, pcm16: bytes) -> float:
        if not pcm16:
            return 0.0
        try:
            arr = np.frombuffer(pcm16, dtype=np.int16)
            if arr.size == 0:
                return 0.0
            return float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
        except Exception:  # noqa: BLE001
            return 0.0

    def is_speech_frame(self, pcm16: bytes) -> bool:
        if len(pcm16) < self.frame_bytes:
            return False
        frame = pcm16[: self.frame_bytes]
        energy = self.frame_energy(frame)
        if self._webrtc is not None:
            try:
                return bool(self._webrtc.is_speech(frame, self.sample_rate)) and energy > 80
            except Exception:  # noqa: BLE001
                pass
        return energy >= self.energy_threshold

    def process(self, pcm16: bytes) -> VADResult:
        speech = self.is_speech_frame(pcm16)
        energy = self.frame_energy(pcm16)
        if speech:
            self._voiced_ms += self.frame_ms
            self._silence_ms = 0
            self._in_utterance = True
        else:
            if self._in_utterance:
                self._silence_ms += self.frame_ms
            else:
                self._silence_ms += self.frame_ms
        return VADResult(
            is_speech=speech,
            energy=energy,
            voiced_ms=self._voiced_ms,
            silence_ms=self._silence_ms,
        )

    def should_finalize(self) -> bool:
        return self._in_utterance and self._silence_ms >= self.silence_hangover_ms

    @property
    def in_utterance(self) -> bool:
        return self._in_utterance


class RingPCMBuffer:
    """Fixed-capacity PCM ring used while waiting for VAD hangover."""

    def __init__(self, max_ms: int, sample_rate: int = 16_000, sample_width: int = 2) -> None:
        self.max_bytes = int(sample_rate * (max_ms / 1000.0) * sample_width)
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def clear(self) -> None:
        self._chunks.clear()
        self._size = 0

    def append(self, data: bytes) -> None:
        self._chunks.append(data)
        self._size += len(data)
        while self._size > self.max_bytes and self._chunks:
            dropped = self._chunks.popleft()
            self._size -= len(dropped)

    def dump(self) -> bytes:
        blob = b"".join(self._chunks)
        self.clear()
        return blob

    def __len__(self) -> int:
        return self._size


def pcm16_to_wav(pcm: bytes, sample_rate: int = 16_000, channels: int = 1) -> bytes:
    """Wrap raw PCM16 as an in-memory WAV for Groq Whisper uploads."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def float_rms(pcm16: bytes) -> float:
    if len(pcm16) < 2:
        return 0.0
    count = len(pcm16) // 2
    shorts = struct.unpack(f"<{count}h", pcm16[: count * 2])
    if not shorts:
        return 0.0
    acc = sum(s * s for s in shorts) / len(shorts)
    return acc ** 0.5
