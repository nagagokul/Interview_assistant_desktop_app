"""Stealth / screen-share exclusion controller."""

from __future__ import annotations

from typing import Any

from src.core.context import CTX
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger
from src.utils.win32_helpers import (
    enable_tool_window,
    force_opaque_layered,
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
        """
        Enable/disable screen-share exclusion while keeping the window visible locally.

        Critical: never combine WDA_EXCLUDEFROMCAPTURE with SetLayeredWindowAttributes
        alpha blending — that makes the overlay invisible to the candidate on many PCs.
        """
        try:
            hwnd = hwnd_from_qt(widget)
        except Exception:  # noqa: BLE001
            log.exception("Unable to resolve HWND")
            return False

        enable_tool_window(hwnd)
        set_window_topmost(hwnd, True)
        ok = set_exclude_from_capture(hwnd, enabled)

        if enabled:
            # Keep fully opaque at the Win32 layered level; visual softness comes
            # from the Qt stylesheet (rgba panel), not LWA_ALPHA.
            force_opaque_layered(hwnd)
            # Qt opacity < 1.0 also uses LWA under the hood — pin to 1.0 in stealth
            try:
                widget.setWindowOpacity(1.0)
            except Exception:  # noqa: BLE001
                pass
        else:
            # Restore user opacity via Qt only (avoid double Win32 alpha)
            try:
                widget.setWindowOpacity(CTX.opacity)
            except Exception:  # noqa: BLE001
                pass

        self._ensure_local_visibility(widget, hwnd)
        log.info(
            "Stealth apply done enabled=%s hwnd=%s opacity_ctx=%.2f",
            enabled,
            hwnd,
            CTX.opacity,
        )
        return ok

    def _ensure_local_visibility(self, widget: Any, hwnd: int) -> None:
        """Force the overlay back onto the local desktop after affinity changes."""
        try:
            if not widget.isVisible():
                widget.show()
            widget.setWindowOpacity(1.0 if CTX.stealth_enabled else max(0.25, CTX.opacity))
            if CTX.stealth_enabled:
                # While stealth is on we keep Win32 alpha at 255; stylesheet handles look
                force_opaque_layered(hwnd)
            widget.raise_()
            widget.showNormal()
            widget.activateWindow()
            widget.update()
            set_window_topmost(hwnd, True)
        except Exception:  # noqa: BLE001
            log.exception("Failed to restore local visibility")

    def set_opacity(self, opacity: float) -> None:
        opacity = max(0.25, min(1.0, float(opacity)))
        CTX.opacity = opacity
        for widget in list(self._widgets):
            try:
                hwnd = hwnd_from_qt(widget)
                if CTX.stealth_enabled:
                    # Stealth mode: do not use Win32/Qt alpha (breaks local visibility).
                    # Softness is stylesheet-only; keep window fully opaque to DWM.
                    force_opaque_layered(hwnd)
                    widget.setWindowOpacity(1.0)
                else:
                    widget.setWindowOpacity(opacity)
                    # Optional Win32 mirror only when not excluding from capture
                    set_layered_alpha(hwnd, opacity)
            except Exception:  # noqa: BLE001
                log.exception("Opacity update failed")
        BUS.publish(EventType.OPACITY_CHANGED, opacity=opacity)

    def reveal(self, widget: Any | None = None) -> None:
        """Emergency: turn stealth off and force-show the overlay."""
        CTX.stealth_enabled = False
        targets = [widget] if widget is not None else list(self._widgets)
        for w in targets:
            if w is None:
                continue
            self.apply_to(w, False)
            try:
                w.show()
                w.raise_()
                w.activateWindow()
            except Exception:  # noqa: BLE001
                log.exception("Reveal failed")
        BUS.publish(EventType.STEALTH_CHANGED, enabled=False)
        BUS.publish(EventType.STATUS, message="Stealth OFF — overlay restored")
