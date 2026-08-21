"""Shared helpers for transient API errors (429 / 503) and retry-after parsing."""

from __future__ import annotations

import re
from typing import Any


_RETRY_AFTER_RE = re.compile(
    r"(?:try again in|retry after|retry-after)[^\d]*(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|ms|m|min|mins|minute|minutes)?",
    re.IGNORECASE,
)


def is_transient_api_error(exc: BaseException | str) -> bool:
    """True for overload / rate-limit style failures that may succeed on retry."""
    msg = str(exc)
    lower = msg.lower()
    needles = (
        "503",
        "429",
        "unavailable",
        "high demand",
        "rate limit",
        "rate_limit",
        "resource_exhausted",
        "too many requests",
        "temporarily",
        "overloaded",
        "quota exceeded",
    )
    return any(n in lower for n in needles)


def is_not_found_model_error(exc: BaseException | str) -> bool:
    msg = str(exc)
    lower = msg.lower()
    return "404" in msg or "not_found" in lower or "not found" in lower


def parse_retry_after_seconds(exc: BaseException | str, default: float = 3.0) -> float:
    """Extract 'try again in Ns' style delays from provider error text."""
    msg = str(exc)
    m = _RETRY_AFTER_RE.search(msg)
    if not m:
        return max(0.5, float(default))
    value = float(m.group(1))
    unit = (m.group(2) or "s").lower()
    if unit in ("ms",):
        return max(0.2, value / 1000.0)
    if unit in ("m", "min", "mins", "minute", "minutes"):
        return max(0.5, value * 60.0)
    return max(0.2, value)


def http_status_from_exc(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def format_api_error(prefix: str, exc: Any) -> str:
    status = http_status_from_exc(exc) if isinstance(exc, BaseException) else None
    body = str(exc)
    if status == 429 or "429" in body:
        return f"{prefix} rate-limited (429). Waiting for free-tier RPM window to recover."
    if status == 503 or "503" in body or "unavailable" in body.lower():
        return f"{prefix} temporarily unavailable (503). Retrying / falling back…"
    return f"{prefix}: {body}"
