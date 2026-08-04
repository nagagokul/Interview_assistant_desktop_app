"""Global hotkey service (keyboard library) bridged to the EventBus."""

from __future__ import annotations

import threading
from typing import Callable

from src.core.config import CONFIG
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger

log = get_logger("hotkeys")


class KeyHookService:
    """
    Registers OS-level hotkeys without requiring focus on the overlay.

    Default bindings:
      Alt+H     — toggle overlay visibility
      Alt+S     — open snipping / OCR region selector
      Alt+Enter — trigger AI answer from current context
    """

    def __init__(self) -> None:
        self._registered: list[str] = []
        self._lock = threading.Lock()
        self._active = False
        self._handlers: dict[str, Callable[[], None]] = {}

    def start(self) -> None:
        if self._active:
            return
        try:
            import keyboard  # type: ignore
        except Exception:  # noqa: BLE001
            log.exception("keyboard package unavailable — hotkeys disabled")
            BUS.publish(EventType.STATUS, message="Hotkeys unavailable (install keyboard)")
            return

        mapping = {
            CONFIG.ui.hotkey_toggle: "toggle_overlay",
            CONFIG.ui.hotkey_snip: "snip_region",
            CONFIG.ui.hotkey_ask: "ask_ai",
        }
        with self._lock:
            for combo, action in mapping.items():
                try:
                    keyboard.add_hotkey(combo, lambda a=action: self._emit(a), suppress=False)
                    self._registered.append(combo)
                    log.info("Hotkey registered: %s → %s", combo, action)
                except Exception:  # noqa: BLE001
                    log.exception("Failed to register hotkey %s", combo)
        self._active = True

    def stop(self) -> None:
        if not self._active:
            return
        try:
            import keyboard

            for combo in self._registered:
                try:
                    keyboard.remove_hotkey(combo)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        self._registered.clear()
        self._active = False
        log.info("Hotkeys unregistered")

    def on(self, action: str, callback: Callable[[], None]) -> None:
        self._handlers[action] = callback

    def _emit(self, action: str) -> None:
        BUS.publish(EventType.HOTKEY, action=action)
        cb = self._handlers.get(action)
        if cb:
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("Hotkey handler failed for %s", action)
