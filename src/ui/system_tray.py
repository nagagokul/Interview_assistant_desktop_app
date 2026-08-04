"""System tray icon and context menu."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from src.core.paths import icons_dir


def _build_fallback_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(70, 120, 220))
    painter.setPen(QColor(180, 210, 255))
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(QColor(230, 240, 255))
    painter.drawText(pix.rect(), 0x84, "IC")  # AlignHCenter | AlignVCenter
    painter.end()
    return QIcon(pix)


def load_app_icon() -> QIcon:
    for name in ("tray.png", "app.ico", "app.png"):
        path = icons_dir() / name
        if path.is_file():
            return QIcon(str(path))
    return _build_fallback_icon()


class SystemTray(QSystemTrayIcon):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(load_app_icon(), parent)
        self.setToolTip("Interview Copilot")
        menu = QMenu()
        self.action_show = QAction("Show / Hide Overlay", menu)
        self.action_listen = QAction("Start Listening", menu)
        self.action_snip = QAction("Select OCR Region", menu)
        self.action_ask = QAction("Ask AI (context)", menu)
        self.action_stealth = QAction("Toggle Stealth", menu)
        self.action_quit = QAction("Quit", menu)
        for a in (
            self.action_show,
            self.action_listen,
            self.action_snip,
            self.action_ask,
            self.action_stealth,
            self.action_quit,
        ):
            menu.addAction(a)
        self.setContextMenu(menu)
        self.show()
