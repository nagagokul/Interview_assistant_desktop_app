"""
Windows WASAPI loopback openers.

sounddevice 0.5.x WasapiSettings accepts ONLY:
  exclusive, auto_convert, explicit_sample_format
It does NOT accept loopback=True — that caused the production crash:

  WasapiSettings.__init__() got an unexpected keyword argument 'loopback'

True system-audio capture on Windows 10/11 requires one of:
  1) PyAudioWPatch — exposes WASAPI loopback devices as real inputs
  2) soundcard     — Microphone(..., include_loopback=True)
  3) Stereo Mix / named loopback as a normal sounddevice input
     (WasapiSettings(exclusive=False) only — never loopback=)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

from src.core.logging_setup import get_logger

log = get_logger("wasapi")


@dataclass
class LoopbackHandle:
    """Normalized pull-based loopback reader → float32 mono chunks at sample_rate."""

    name: str
    sample_rate: int
    channels: int
    read: Callable[[int], np.ndarray]  # frames -> shape (frames,) float32 mono
    close: Callable[[], None]


def _to_mono_f32(block: np.ndarray) -> np.ndarray:
    arr = np.asarray(block, dtype=np.float32)
    if arr.ndim == 1:
        return np.clip(arr, -1.0, 1.0)
    if arr.ndim == 2:
        return np.clip(arr.mean(axis=1), -1.0, 1.0)
    return np.clip(arr.reshape(-1).astype(np.float32), -1.0, 1.0)


def resample_mono_f32(mono: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Lightweight linear resampler (no SciPy dependency)."""
    if src_rate == dst_rate or mono.size == 0:
        return mono.astype(np.float32, copy=False)
    n_dst = max(1, int(round(len(mono) * float(dst_rate) / float(src_rate))))
    x_old = np.linspace(0.0, 1.0, num=len(mono), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_dst, endpoint=False)
    return np.interp(x_new, x_old, mono.astype(np.float64)).astype(np.float32)


def _wasapi_extra_settings(sd: object) -> object | None:
    """Build WasapiSettings WITHOUT the invalid loopback= keyword."""
    WasapiSettings = getattr(sd, "WasapiSettings", None)
    if WasapiSettings is None:
        return None
    for kwargs in (
        {"exclusive": False, "auto_convert": True},
        {"exclusive": False},
        {},
    ):
        try:
            return WasapiSettings(**kwargs)
        except TypeError:
            continue
        except Exception:  # noqa: BLE001
            return None
    return None


def open_loopback_pyaudiowpatch(target_rate: int = 16_000) -> LoopbackHandle | None:
    """Preferred: true WASAPI loopback via PyAudioWPatch virtual input devices."""
    try:
        import pyaudiowpatch as pyaudio  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"[AUDIO CAPTURED] PyAudioWPatch unavailable: {exc}", flush=True)
        return None

    p = pyaudio.PyAudio()
    try:
        loopback = None

        # Prefer helper when present (newer PyAudioWPatch)
        getter = getattr(p, "get_default_wasapi_loopback", None)
        if callable(getter):
            try:
                loopback = getter()
            except Exception as exc:  # noqa: BLE001
                print(f"[AUDIO CAPTURED] get_default_wasapi_loopback failed: {exc}", flush=True)

        if loopback is None:
            try:
                wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                default_out = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            except Exception as exc:  # noqa: BLE001
                print(f"[AUDIO CAPTURED] PyAudioWPatch WASAPI host missing: {exc}", flush=True)
                p.terminate()
                return None

            if default_out.get("isLoopbackDevice"):
                loopback = default_out
            else:
                for device in p.get_loopback_device_info_generator():
                    if default_out["name"] in device["name"]:
                        loopback = device
                        break
                if loopback is None:
                    for device in p.get_loopback_device_info_generator():
                        loopback = device
                        break

        if loopback is None:
            p.terminate()
            print("[AUDIO CAPTURED] PyAudioWPatch: no loopback device found", flush=True)
            return None

        # Device native rate/channels — PortAudio rejects arbitrary rates on many WASAPI LBs
        rate = int(loopback.get("defaultSampleRate") or target_rate)
        channels = max(1, int(loopback.get("maxInputChannels") or 2))
        index = int(loopback["index"])
        print(
            f"[AUDIO CAPTURED] PyAudioWPatch loopback idx={index} "
            f"name={loopback['name']!r} ch={channels} sr={rate}",
            flush=True,
        )

        stream = p.open(
            format=pyaudio.paFloat32,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=index,
            frames_per_buffer=max(256, int(rate * 0.03)),
        )

        def _read(frames: int) -> np.ndarray:
            raw = stream.read(max(1, frames), exception_on_overflow=False)
            data = np.frombuffer(raw, dtype=np.float32)
            if channels > 1:
                data = data.reshape(-1, channels)
            return _to_mono_f32(data)

        def _close() -> None:
            try:
                if stream.is_active():
                    stream.stop_stream()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass

        return LoopbackHandle(
            name=f"PyAudioWPatch:{loopback['name']}",
            sample_rate=rate,
            channels=channels,
            read=_read,
            close=_close,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[AUDIO CAPTURED] PyAudioWPatch open failed: {exc}", flush=True)
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
        return None


def open_loopback_soundcard(target_rate: int = 16_000) -> LoopbackHandle | None:
    """Fallback: soundcard Speaker → loopback microphone."""
    try:
        import soundcard as sc  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"[AUDIO CAPTURED] soundcard unavailable: {exc}", flush=True)
        return None

    try:
        speaker = sc.default_speaker()
        mic = None

        # Prefer explicit loopback mics matching the default speaker
        try:
            mics = sc.all_microphones(include_loopback=True)
            loopbacks = [m for m in mics if getattr(m, "isloopback", False)]
            for m in loopbacks:
                if speaker.name in m.name or m.name in speaker.name:
                    mic = m
                    break
            if mic is None and loopbacks:
                mic = loopbacks[0]
        except Exception as exc:  # noqa: BLE001
            print(f"[AUDIO CAPTURED] soundcard all_microphones failed: {exc}", flush=True)

        if mic is None:
            # id may be name or id depending on soundcard version
            for candidate in (getattr(speaker, "id", None), speaker.name):
                if candidate is None:
                    continue
                try:
                    mic = sc.get_microphone(id=candidate, include_loopback=True)
                    break
                except Exception:  # noqa: BLE001
                    continue

        if mic is None:
            print("[AUDIO CAPTURED] soundcard: no loopback microphone", flush=True)
            return None

        print(
            f"[AUDIO CAPTURED] soundcard loopback speaker={speaker.name!r} mic={mic.name!r}",
            flush=True,
        )
        recorder = mic.recorder(samplerate=target_rate, channels=1)
        recorder.__enter__()

        def _read(frames: int) -> np.ndarray:
            data = recorder.record(numframes=max(1, frames))
            return _to_mono_f32(data)

        def _close() -> None:
            try:
                recorder.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

        return LoopbackHandle(
            name=f"soundcard:{mic.name}",
            sample_rate=target_rate,
            channels=1,
            read=_read,
            close=_close,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[AUDIO CAPTURED] soundcard loopback failed: {exc}", flush=True)
        return None


def open_loopback_stereo_mix(target_rate: int = 16_000) -> LoopbackHandle | None:
    """
    Last-resort: Stereo Mix / Wave Out Mix / named loopback as a normal input.

    Uses WasapiSettings(exclusive=False[, auto_convert=True]) only — never loopback=.
    """
    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        print(f"[AUDIO CAPTURED] sounddevice unavailable for stereo-mix: {exc}", flush=True)
        return None

    try:
        idx = None
        name = ""
        channels = 2
        for i, d in enumerate(sd.query_devices()):
            n = str(d["name"]).lower()
            max_in = int(d.get("max_input_channels", 0) or 0)
            if max_in <= 0:
                continue
            try:
                host = sd.query_hostapis(d["hostapi"])["name"].lower()
            except Exception:
                host = ""
            named = any(k in n for k in ("stereo mix", "wave out mix", "what u hear", "loopback"))
            if named or ("wasapi" in host and "loopback" in n):
                idx = i
                name = d["name"]
                channels = min(2, max_in)
                break

        if idx is None:
            print("[AUDIO CAPTURED] no Stereo Mix / named loopback input found", flush=True)
            return None

        print(
            f"[AUDIO CAPTURED] Stereo Mix / named loopback idx={idx} "
            f"name={name!r} ch={channels}",
            flush=True,
        )

        q: list[np.ndarray] = []
        lock = threading.Lock()
        stop = threading.Event()
        remainder = np.zeros(0, dtype=np.float32)

        def _callback(indata, frames, time_info, status):  # noqa: ANN001, ARG001
            if stop.is_set():
                raise sd.CallbackStop()
            with lock:
                q.append(indata.copy())

        extra = _wasapi_extra_settings(sd)
        stream = sd.InputStream(
            samplerate=target_rate,
            channels=channels,
            dtype="float32",
            device=idx,
            callback=_callback,
            extra_settings=extra,
        )
        stream.start()

        def _read(frames: int) -> np.ndarray:
            nonlocal remainder
            needed = frames
            parts: list[np.ndarray] = []
            if len(remainder):
                take = min(len(remainder), needed)
                parts.append(remainder[:take])
                remainder = remainder[take:]
                needed -= take
            spins = 0
            while needed > 0 and spins < 200:
                with lock:
                    block = q.pop(0) if q else None
                if block is None:
                    stop.wait(0.005)
                    spins += 1
                    continue
                mono = _to_mono_f32(block)
                if len(mono) <= needed:
                    parts.append(mono)
                    needed -= len(mono)
                else:
                    parts.append(mono[:needed])
                    remainder = mono[needed:]
                    needed = 0
            if not parts:
                return np.zeros(frames, dtype=np.float32)
            data = np.concatenate(parts)
            if len(data) < frames:
                return np.pad(data, (0, frames - len(data)))
            return data[:frames]

        def _close() -> None:
            stop.set()
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass

        return LoopbackHandle(
            name=f"StereoMix:{name}",
            sample_rate=target_rate,
            channels=channels,
            read=_read,
            close=_close,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[AUDIO CAPTURED] Stereo Mix open failed: {exc}", flush=True)
        return None


def open_wasapi_loopback(target_rate: int = 16_000) -> LoopbackHandle:
    """
    Open the best available loopback source.
    Raises RuntimeError if none work (caller must emit error + keep GUI alive).
    """
    for opener, label in (
        (open_loopback_pyaudiowpatch, "PyAudioWPatch"),
        (open_loopback_soundcard, "soundcard"),
        (open_loopback_stereo_mix, "StereoMix"),
    ):
        print(f"[AUDIO CAPTURED] trying loopback via {label}…", flush=True)
        handle = opener(target_rate=target_rate)
        if handle is not None:
            print(f"[AUDIO CAPTURED] WASAPI loopback READY via {handle.name}", flush=True)
            return handle
    raise RuntimeError(
        "Failed to enable WASAPI loopback: no backend available. "
        "Install: pip install PyAudioWPatch soundcard  "
        "OR enable Stereo Mix in Windows Sound settings. "
        "(sounddevice WasapiSettings does not accept loopback= — that API was removed/never shipped.)"
    )
