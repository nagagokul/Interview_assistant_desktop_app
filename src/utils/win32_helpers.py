"""Windows API helpers: display affinity (stealth), layered windows, DPI."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

from src.core.logging_setup import get_logger

log = get_logger("win32")

# SetWindowDisplayAffinity values
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Win10 2004+

# Extended window styles
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020

HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

LWA_ALPHA = 0x00000002


def is_windows() -> bool:
    return sys.platform == "win32"


class _WinAPI:
    """Lazy-bound user32 / kernel32 wrappers (safe to import on non-Windows)."""

    def __init__(self) -> None:
        self.available = False
        self.user32: Any = None
        self.kernel32: Any = None
        if not is_windows():
            return
        try:
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            self.user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
            self.user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

            self.user32.GetWindowDisplayAffinity.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            self.user32.GetWindowDisplayAffinity.restype = wintypes.BOOL

            self.user32.SetWindowLongPtrW = getattr(
                self.user32, "SetWindowLongPtrW", self.user32.SetWindowLongW
            )
            self.user32.GetWindowLongPtrW = getattr(
                self.user32, "GetWindowLongPtrW", self.user32.GetWindowLongW
            )

            self.user32.SetLayeredWindowAttributes.argtypes = [
                wintypes.HWND,
                wintypes.COLORREF,
                wintypes.BYTE,
                wintypes.DWORD,
            ]
            self.user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

            self.user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            self.user32.SetWindowPos.restype = wintypes.BOOL

            self.available = True
        except Exception:  # noqa: BLE001
            log.exception("Failed to bind Win32 APIs")
            self.available = False


_API = _WinAPI()


def hwnd_from_qt(widget: Any) -> int:
    """Extract native HWND from a Qt widget."""
    wid = int(widget.winId())
    return wid


def set_exclude_from_capture(hwnd: int, enabled: bool = True) -> bool:
    """
    Apply WDA_EXCLUDEFROMCAPTURE so the window is invisible to screen-share
    capture APIs (Zoom, Teams, Meet, Discord Desktop Capture, etc.) while
    remaining visible on the local desktop.
    """
    if not _API.available:
        log.warning("SetWindowDisplayAffinity unavailable on this platform")
        return False
    affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
    ok = bool(_API.user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), affinity))
    if not ok:
        err = ctypes.get_last_error()
        log.error("SetWindowDisplayAffinity failed (err=%s) hwnd=%s", err, hwnd)
        # Fallback to WDA_MONITOR on older builds
        if enabled:
            ok = bool(_API.user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_MONITOR))
            if ok:
                log.info("Fell back to WDA_MONITOR for hwnd=%s", hwnd)
    else:
        log.info("Display affinity set to %s for hwnd=%s", hex(affinity), hwnd)
    return ok


def set_window_topmost(hwnd: int, topmost: bool = True) -> bool:
    if not _API.available:
        return False
    insert_after = HWND_TOPMOST if topmost else 0
    return bool(
        _API.user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(insert_after) if topmost else wintypes.HWND(0),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    )


def set_layered_alpha(hwnd: int, opacity: float) -> bool:
    """opacity in [0.0, 1.0] -> Win32 alpha byte."""
    if not _API.available:
        return False
    alpha = max(0, min(255, int(opacity * 255)))
    # Ensure WS_EX_LAYERED
    style = _API.user32.GetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE)
    if not (style & WS_EX_LAYERED):
        _API.user32.SetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE, style | WS_EX_LAYERED)
    return bool(
        _API.user32.SetLayeredWindowAttributes(
            wintypes.HWND(hwnd), 0, alpha, LWA_ALPHA
        )
    )


def enable_tool_window(hwnd: int) -> bool:
    """Hide from taskbar / Alt-Tab via WS_EX_TOOLWINDOW."""
    if not _API.available:
        return False
    style = _API.user32.GetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE)
    style = (style | WS_EX_TOOLWINDOW | WS_EX_LAYERED) & ~0x00040000  # clear WS_EX_APPWINDOW
    _API.user32.SetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE, style)
    return True


def set_click_through(hwnd: int, enabled: bool) -> bool:
    if not _API.available:
        return False
    style = _API.user32.GetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE)
    if enabled:
        style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    else:
        style &= ~WS_EX_TRANSPARENT
        style |= WS_EX_LAYERED
    _API.user32.SetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE, style)
    return True
