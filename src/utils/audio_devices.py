"""Resolve sounddevice default input/output indices across SDK versions."""

from __future__ import annotations

from typing import Any


def _coerce_index(value: Any) -> int | None:
    """Convert assorted sounddevice default-device shapes into an int index."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    # Newer sounddevice: _InputOutputPair(input=..., output=...)
    for attr in ("input", "output"):
        if hasattr(value, attr):
            # Caller picks which attr — don't use here
            break
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def split_default_device(default: Any) -> tuple[int | None, int | None]:
    """
    Return (input_index, output_index) from sd.default.device.

    Handles:
      - int
      - (in, out) list/tuple
      - sounddevice._InputOutputPair with .input / .output
      - mapping-like objects
    """
    if default is None:
        return None, None

    # _InputOutputPair / object with attributes
    if hasattr(default, "input") or hasattr(default, "output"):
        inn = getattr(default, "input", None)
        out = getattr(default, "output", None)
        return _coerce_index(inn), _coerce_index(out)

    # Sequence
    if isinstance(default, (list, tuple)):
        inn = _coerce_index(default[0]) if len(default) >= 1 else None
        out = _coerce_index(default[1]) if len(default) >= 2 else None
        return inn, out

    # Mapping
    if isinstance(default, dict):
        inn = _coerce_index(default.get("input", default.get(0)))
        out = _coerce_index(default.get("output", default.get(1)))
        return inn, out

    # Bare int-like
    idx = _coerce_index(default)
    return idx, idx


def resolve_mic_device(explicit: int | None = None) -> int | None:
    """
    Resolve default microphone index.
    Returns None to mean 'use sounddevice system default' (valid for InputStream).
    """
    if explicit is not None:
        return explicit
    try:
        import sounddevice as sd

        inn, _ = split_default_device(sd.default.device)
        if inn is not None:
            return inn
        # Query first device with input channels
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0) or 0) > 0:
                return i
    except Exception:
        pass
    return None  # sounddevice accepts device=None as default input


def resolve_loopback_device(explicit: int | None = None) -> int | None:
    """
    Resolve WASAPI loopback device index (usually the default OUTPUT device).
    """
    if explicit is not None:
        return explicit
    try:
        import sounddevice as sd

        # Named loopback / stereo mix first
        for i, d in enumerate(sd.query_devices()):
            name = str(d.get("name", "")).lower()
            try:
                host = sd.query_hostapis(d["hostapi"])["name"].lower()
            except Exception:
                host = ""
            if "wasapi" in host and int(d.get("max_input_channels", 0) or 0) > 0:
                if "loopback" in name or "stereo mix" in name or "what u hear" in name:
                    return i

        _, out = split_default_device(sd.default.device)
        if out is not None:
            return out

        # Fallback: first WASAPI output-capable device
        for i, d in enumerate(sd.query_devices()):
            try:
                host = sd.query_hostapis(d["hostapi"])["name"].lower()
            except Exception:
                host = ""
            if "wasapi" in host and int(d.get("max_output_channels", 0) or 0) > 0:
                return i
    except Exception:
        pass
    return None


def device_hostapi_name(device: int | None, sd: Any | None = None) -> str:
    """Return host API name for a sounddevice device index ('' if unknown)."""
    try:
        if sd is None:
            import sounddevice as sd  # type: ignore
        if device is None:
            inn, _ = split_default_device(sd.default.device)
            device = inn
        if device is None:
            return ""
        info = sd.query_devices(device)
        return str(sd.query_hostapis(info["hostapi"])["name"])
    except Exception:
        return ""


def is_wasapi_device(device: int | None, sd: Any | None = None) -> bool:
    return "wasapi" in device_hostapi_name(device, sd).lower()


def wasapi_extra_settings_for_device(device: int | None, sd: Any) -> Any | None:
    """
    Return WasapiSettings ONLY when the target device is on Windows WASAPI.

    Passing WasapiSettings to MME/DirectSound/WDM-KS devices yields:
      PaErrorCode -9984 (paIncompatibleHostApiSpecificStreamInfo)
    """
    if not is_wasapi_device(device, sd):
        return None
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
        except Exception:
            return None
    return None
