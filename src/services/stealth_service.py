"""Stealth / screen-share exclusion controller."""

from __future__ import annotations

from typing import Any

from src.core.context import CTX
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger
from src.utils.win32_helpers import (
    enable_tool_window,
    hwnd_from_qt,
    set_exclude_from_capture,
    set_layered_alpha,
    set_window_topmost,
)

log = get_logger("stealth")


class StealthService:
    """Applies native Windows capture-exclusion flags to Qt overlay windows."""

    def __init__(self) -> None:
        self._widgets: list[Any] = []

    def register(self, widget: Any) -> None:
        if widget not in self._widgets:
            self._widgets.append(widget)

    def unregister(self, widget: Any) -> None:
        if widget in self._widgets:
            self._widgets.remove(widget)

    def apply_all(self, enabled: bool | None = None) -> None:
        if enabled is None:
            enabled = CTX.stealth_enabled
        else:
            CTX.stealth_enabled = enabled
        for widget in list(self._widgets):
            self.apply_to(widget, enabled)
        BUS.publish(EventType.STEALTH_CHANGED, enabled=enabled)

    def apply_to(self, widget: Any, enabled: bool = True) -> bool:
        try:
            hwnd = hwnd_from_qt(widget)
        except Exception:  # noqa: BLE001
            log.exception("Unable to resolve HWND")
            return False
        enable_tool_window(hwnd)
        set_window_topmost(hwnd, True)
        ok = set_exclude_from_capture(hwnd, enabled)
        # Re-apply opacity via Win32 layered attributes for consistency
        set_layered_alpha(hwnd, CTX.opacity)
        return ok

    def set_opacity(self, opacity: float) -> None:
        opacity = max(0.25, min(1.0, float(opacity)))
        CTX.opacity = opacity
        for widget in list(self._widgets):
            try:
                hwnd = hwnd_from_qt(widget)
                set_layered_alpha(hwnd, opacity)
                # Also keep Qt opacity in sync
                widget.setWindowOpacity(opacity)
            except Exception:  # noqa: BLE001
                log.exception("Opacity update failed")
        BUS.publish(EventType.OPACITY_CHANGED, opacity=opacity)
