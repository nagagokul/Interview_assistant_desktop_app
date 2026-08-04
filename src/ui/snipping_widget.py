"""Frameless transparent snipping overlay for OCR region selection."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from src.core.logging_setup import get_logger

log = get_logger("snip")


class SnippingWidget(QWidget):
    """
    Full-screen translucent overlay. User drag-draws a rectangle;
    emits regionSelected(left, top, right, bottom) in global coords.
    """

    regionSelected = pyqtSignal(int, int, int, int)
    cancelled = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._origin = QPoint()
        self._current = QPoint()
        self._drawing = False
        self._rubber = QRect()

    def begin(self) -> None:
        # Cover the virtual desktop (all monitors)
        screens = QGuiApplication.screens()
        if not screens:
            return
        geo = screens[0].geometry()
        for s in screens[1:]:
            geo = geo.united(s.geometry())
        self.setGeometry(geo)
        self._drawing = False
        self._rubber = QRect()
        self.showFullScreen()
        self.activateWindow()
        self.raise_()
        log.info("Snipping overlay active covering %s", geo)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Dim the desktop
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if not self._rubber.isNull():
            # Clear the selected region (less dim) and draw border
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(self._rubber, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(QColor(120, 170, 255), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(self._rubber.adjusted(0, 0, -1, -1))
            # Dimension label
            painter.setPen(QColor(220, 230, 255))
            label = f"{self._rubber.width()} × {self._rubber.height()}"
            painter.drawText(self._rubber.topLeft() + QPoint(6, -8), label)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self._drawing = True
            self._rubber = QRect(self._origin, self._current)
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._drawing:
            self._current = event.position().toPoint()
            self._rubber = QRect(self._origin, self._current).normalized()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            self._drawing = False
            rect = QRect(self._origin, event.position().toPoint()).normalized()
            self.hide()
            if rect.width() < 8 or rect.height() < 8:
                self.cancelled.emit()
                return
            # Map widget-local rect to global virtual-desktop coords
            top_left = self.mapToGlobal(rect.topLeft())
            bottom_right = self.mapToGlobal(rect.bottomRight())
            left, top = top_left.x(), top_left.y()
            right, bottom = bottom_right.x() + 1, bottom_right.y() + 1
            log.info("Region selected %s", (left, top, right, bottom))
            self.regionSelected.emit(left, top, right, bottom)

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.cancelled.emit()
