"""
Client-side API usage meters for Groq Whisper + Gemini free-tier limits.

Tracks rolling RPM / daily request counts so the UI can warn before hard
429/quota failures. Limits are configurable (defaults match common free tiers).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Callable, Deque, Literal

from src.core.config import CONFIG
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger

log = get_logger("api_usage")

Provider = Literal["groq", "gemini"]


@dataclass(frozen=True)
class ProviderUsageSnapshot:
    provider: Provider
    label: str
    rpm_used: int
    rpm_limit: int
    daily_used: int
    daily_limit: int
    warn: bool
    critical: bool
    message: str

    @property
    def rpm_ratio(self) -> float:
        if self.rpm_limit <= 0:
            return 0.0
        return self.rpm_used / float(self.rpm_limit)

    @property
    def daily_ratio(self) -> float:
        if self.daily_limit <= 0:
            return 0.0
        return self.daily_used / float(self.daily_limit)


class ApiUsageTracker:
    """Thread-safe rolling + daily request counters for STT/LLM providers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._groq_ts: Deque[float] = deque()
        self._gemini_ts: Deque[float] = deque()
        self._day: date = date.today()
        self._groq_daily = 0
        self._gemini_daily = 0
        self._warned: set[str] = set()
        self._listeners: list[Callable[[ProviderUsageSnapshot], None]] = []

    # ---- config helpers ----

    def _limits(self, provider: Provider) -> tuple[int, int, float, float]:
        cfg = CONFIG.ai
        if provider == "groq":
            return (
                max(1, int(cfg.groq_rpm_limit)),
                max(1, int(cfg.groq_daily_limit)),
                float(cfg.usage_warn_ratio),
                float(cfg.usage_critical_ratio),
            )
        return (
            max(1, int(cfg.gemini_rpm_limit)),
            max(1, int(cfg.gemini_daily_limit)),
            float(cfg.usage_warn_ratio),
            float(cfg.usage_critical_ratio),
        )

    def _roll_day(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._groq_daily = 0
            self._gemini_daily = 0
            self._warned.clear()

    def _prune(self, bucket: Deque[float], now: float, window_sec: float = 60.0) -> None:
        while bucket and now - bucket[0] > window_sec:
            bucket.popleft()

    def _snapshot_locked(self, provider: Provider, now: float | None = None) -> ProviderUsageSnapshot:
        self._roll_day()
        now = time.time() if now is None else now
        rpm_limit, daily_limit, warn_ratio, crit_ratio = self._limits(provider)
        if provider == "groq":
            self._prune(self._groq_ts, now)
            rpm_used = len(self._groq_ts)
            daily_used = self._groq_daily
            label = "Groq STT"
        else:
            self._prune(self._gemini_ts, now)
            rpm_used = len(self._gemini_ts)
            daily_used = self._gemini_daily
            label = "Gemini AI"

        rpm_r = rpm_used / float(rpm_limit)
        day_r = daily_used / float(daily_limit)
        peak = max(rpm_r, day_r)
        critical = peak >= crit_ratio
        warn = (not critical) and peak >= warn_ratio

        if critical:
            if day_r >= crit_ratio:
                message = (
                    f"{label} near daily free limit ({daily_used}/{daily_limit}). "
                    "Slow down or upgrade the API tier."
                )
            else:
                message = (
                    f"{label} near free RPM limit ({rpm_used}/{rpm_limit}/min). "
                    "Requests may fail with 429 until the window resets."
                )
        elif warn:
            message = (
                f"{label} usage high — RPM {rpm_used}/{rpm_limit}, "
                f"today {daily_used}/{daily_limit}"
            )
        else:
            message = f"{label}: {rpm_used}/{rpm_limit} RPM · {daily_used}/{daily_limit} today"

        return ProviderUsageSnapshot(
            provider=provider,
            label=label,
            rpm_used=rpm_used,
            rpm_limit=rpm_limit,
            daily_used=daily_used,
            daily_limit=daily_limit,
            warn=warn,
            critical=critical,
            message=message,
        )

    # ---- public API ----

    def add_listener(self, cb: Callable[[ProviderUsageSnapshot], None]) -> None:
        with self._lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def snapshot(self, provider: Provider) -> ProviderUsageSnapshot:
        with self._lock:
            return self._snapshot_locked(provider)

    def snapshots(self) -> tuple[ProviderUsageSnapshot, ProviderUsageSnapshot]:
        with self._lock:
            return self._snapshot_locked("groq"), self._snapshot_locked("gemini")

    def record(self, provider: Provider, *, count: int = 1) -> ProviderUsageSnapshot:
        """Record successful or attempted API calls and emit warnings when needed."""
        with self._lock:
            self._roll_day()
            now = time.time()
            if provider == "groq":
                for _ in range(max(1, count)):
                    self._groq_ts.append(now)
                    self._groq_daily += 1
            else:
                for _ in range(max(1, count)):
                    self._gemini_ts.append(now)
                    self._gemini_daily += 1
            snap = self._snapshot_locked(provider, now)
            listeners = list(self._listeners)

        self._maybe_intimate(snap)
        for cb in listeners:
            try:
                cb(snap)
            except Exception:  # noqa: BLE001
                log.exception("usage listener failed")
        try:
            BUS.publish(
                EventType.API_USAGE,
                provider=snap.provider,
                message=snap.message,
                warn=snap.warn,
                critical=snap.critical,
                rpm_used=snap.rpm_used,
                rpm_limit=snap.rpm_limit,
                daily_used=snap.daily_used,
                daily_limit=snap.daily_limit,
            )
        except Exception:  # noqa: BLE001
            pass
        return snap

    def would_exceed_rpm(self, provider: Provider, headroom: int = 1) -> bool:
        """True when another call would hit/exceed the configured RPM cap."""
        with self._lock:
            snap = self._snapshot_locked(provider)
            return snap.rpm_used + headroom > snap.rpm_limit

    def seconds_until_rpm_slot(self, provider: Provider) -> float:
        """Seconds to wait until at least one RPM slot frees (0 if available)."""
        with self._lock:
            self._roll_day()
            now = time.time()
            rpm_limit, _, _, _ = self._limits(provider)
            bucket = self._groq_ts if provider == "groq" else self._gemini_ts
            self._prune(bucket, now)
            if len(bucket) < rpm_limit:
                return 0.0
            oldest = bucket[0]
            return max(0.05, 60.0 - (now - oldest) + 0.05)

    def _maybe_intimate(self, snap: ProviderUsageSnapshot) -> None:
        key = f"{snap.provider}:{'crit' if snap.critical else 'warn' if snap.warn else 'ok'}"
        if not snap.warn and not snap.critical:
            # Allow re-warn after recovering below threshold
            self._warned.discard(f"{snap.provider}:warn")
            self._warned.discard(f"{snap.provider}:crit")
            return
        if key in self._warned:
            return
        self._warned.add(key)
        level = "CRITICAL" if snap.critical else "WARN"
        print(f"[API USAGE {level}] {snap.message}", flush=True)
        log.warning("%s", snap.message)
        try:
            BUS.publish(EventType.STATUS, message=snap.message)
        except Exception:  # noqa: BLE001
            pass


# Process singleton
USAGE = ApiUsageTracker()
