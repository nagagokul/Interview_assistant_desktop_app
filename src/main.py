"""
Interview Copilot — application bootstrap.

PyQt6 entrypoint: system tray, global hotkeys, service wiring, overlay window.
"""

from __future__ import annotations

import os
import sys
import traceback


def _bootstrap_path() -> None:
    """Ensure repo root is importable when launched as a script."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    if root not in sys.path:
        sys.path.insert(0, root)


_bootstrap_path()

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.config import CONFIG
from src.core.logging_setup import get_logger, setup_logging
from src.core.paths import appdata_dir
from src.services.ai_orchestrator import AIOrchestrator
from src.services.audio_service import AudioCaptureService
from src.services.key_hook_service import KeyHookService
from src.services.ocr_service import OCRRegionService
from src.services.rag_service import RAGManager
from src.services.stealth_service import StealthService
from src.ui.system_tray import SystemTray
from src.ui.ui_dashboard import OverlayDashboard


def _enable_dpi() -> None:
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass


def main() -> int:
    setup_logging(CONFIG.log_level)
    log = get_logger("main")
    log.info("Starting Interview Copilot — data dir %s", appdata_dir())

    _enable_dpi()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Interview Copilot")
    app.setOrganizationName("Copilot")

    missing = CONFIG.validate_keys()
    if missing:
        log.warning("Missing API keys: %s — configure .env before using AI/STT", ", ".join(missing))

    # Core services (in-process memory slots — no local ports)
    audio = AudioCaptureService()
    ocr = OCRRegionService()
    ai = AIOrchestrator()
    rag = RAGManager()
    stealth = StealthService()
    hotkeys = KeyHookService()

    dashboard = OverlayDashboard(audio=audio, ocr=ocr, ai=ai, rag=rag, stealth=stealth)

    tray = SystemTray(dashboard)
    tray.action_show.triggered.connect(dashboard.toggle_visibility)
    tray.action_listen.triggered.connect(dashboard.toggle_listen)
    tray.action_snip.triggered.connect(dashboard.start_snip)
    tray.action_ask.triggered.connect(dashboard.ask_ai)
    tray.action_stealth.triggered.connect(dashboard.toggle_stealth)

    def _quit() -> None:
        log.info("Quit requested")
        hotkeys.stop()
        dashboard.shutdown()
        app.quit()

    tray.action_quit.triggered.connect(_quit)
    tray.activated.connect(
        lambda reason: dashboard.toggle_visibility()
        if reason == tray.ActivationReason.Trigger
        else None
    )

    hotkeys.on("toggle_overlay", dashboard.toggle_visibility)
    hotkeys.on("snip_region", dashboard.start_snip)
    hotkeys.on("ask_ai", dashboard.ask_ai)
    hotkeys.start()

    dashboard.show()

    if missing:
        QMessageBox.information(
            dashboard,
            "API Keys Required",
            "Add the following to your .env file (next to the EXE or in %APPDATA%\\\\Copilot\\\\.env):\n\n"
            + "\n".join(f"  • {k}" for k in missing)
            + "\n\nSee INSTALL.md for details. Audio/AI features stay disabled until keys are set.",
        )

    code = app.exec()
    hotkeys.stop()
    dashboard.shutdown()
    log.info("Exited with code %s", code)
    return int(code)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
