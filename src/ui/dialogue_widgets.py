"""
Dialogue UI widgets — chronological conversation feed + streaming AI browser.

- LiveConversationFeed: interviewer (left, light blue) / candidate (right, grey)
- AIGuidanceBrowser: QTextBrowser with progressive Markdown/HTML streaming
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.utils.markdown_html import markdown_to_html


class _BubbleRow(QWidget):
    def __init__(self, speaker: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        is_interviewer = speaker == "interviewer"
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        bubble.setMaximumWidth(340)

        if is_interviewer:
            bubble.setStyleSheet(
                "QLabel {"
                " background-color: #2A4A6A;"
                " color: #D6ECFF;"
                " border-radius: 8px;"
                " padding: 8px 10px;"
                " font-size: 12px;"
                "}"
            )
            who = QLabel("Interviewer")
            who.setStyleSheet("color:#7BA3FF;font-size:10px;font-weight:600;")
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(who)
            col.addWidget(bubble)
            wrap = QWidget()
            wrap.setLayout(col)
            row.addWidget(wrap, 0, Qt.AlignmentFlag.AlignLeft)
            row.addStretch(1)
        else:
            bubble.setStyleSheet(
                "QLabel {"
                " background-color: #3A3F48;"
                " color: #E8ECF1;"
                " border-radius: 8px;"
                " padding: 8px 10px;"
                " font-size: 12px;"
                "}"
            )
            who = QLabel("You")
            who.setStyleSheet("color:#A0A8B4;font-size:10px;font-weight:600;")
            who.setAlignment(Qt.AlignmentFlag.AlignRight)
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(who)
            col.addWidget(bubble)
            wrap = QWidget()
            wrap.setLayout(col)
            row.addStretch(1)
            row.addWidget(wrap, 0, Qt.AlignmentFlag.AlignRight)


class LiveConversationFeed(QWidget):
    """Chronological bi-directional dialogue stream (top panel)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;}")

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(4)
        self._scroll.setWidget(self._container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        hdr = QLabel("Live Conversation Stream")
        hdr.setObjectName("TitleLabel")
        root.addWidget(hdr)
        root.addWidget(self._scroll, 1)
        self._count = 0

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._count = 0

    def append_interviewer(self, text: str) -> None:
        self._append("interviewer", text)

    def append_candidate(self, text: str) -> None:
        self._append("candidate", text)

    def _append(self, speaker: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._layout.addWidget(_BubbleRow(speaker, text))
        self._count += 1
        print(
            f"[UI TEXT APPENDED] speaker={speaker} chars={len(text)} total_bubbles={self._count}",
            flush=True,
        )
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())


class AIGuidanceBrowser(QWidget):
    """Dedicated AI viewport — streams Markdown/code without touching the transcript."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raw = ""
        self._autoscroll = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        hdr = QLabel("AI Copilot Core Guidance")
        hdr.setObjectName("TitleLabel")
        root.addWidget(hdr)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setFont(QFont("Segoe UI", 11))
        self.browser.setStyleSheet(
            "QTextBrowser {"
            " background-color: #0B1018;"
            " color: #E8ECF1;"
            " border: 1px solid #2A3548;"
            " border-radius: 6px;"
            " padding: 8px;"
            "}"
        )
        self.browser.setPlaceholderText("AI answers, code, complexity, and follow-ups stream here…")
        root.addWidget(self.browser, 1)

        bar = self.browser.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll)

    def _on_scroll(self, value: int) -> None:
        bar = self.browser.verticalScrollBar()
        self._autoscroll = value >= bar.maximum() - 24

    def begin_stream(self) -> None:
        self._raw = ""
        self._autoscroll = True
        self.browser.setHtml(
            "<div style='color:#7F92B0;font-size:11px;'>Streaming guidance…</div>"
        )
        print("[UI TEXT APPENDED] ai=begin_stream", flush=True)

    def append_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self._raw += chunk
        self.browser.setHtml(markdown_to_html(self._raw))
        print(f"[UI TEXT APPENDED] ai_chunk chars={len(chunk)} total={len(self._raw)}", flush=True)
        if self._autoscroll:
            cursor = self.browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.browser.setTextCursor(cursor)
            self.browser.ensureCursorVisible()
            bar = self.browser.verticalScrollBar()
            bar.setValue(bar.maximum())

    def finalize(self, text: str | None = None) -> None:
        if text:
            self._raw = text
        self.browser.setHtml(markdown_to_html(self._raw))
        print(f"[UI TEXT APPENDED] ai=finalize chars={len(self._raw)}", flush=True)
        if self._autoscroll:
            bar = self.browser.verticalScrollBar()
            bar.setValue(bar.maximum())

    def show_error(self, message: str) -> None:
        from html import escape

        self.browser.setHtml(
            f"<div style='color:#FF8A8A;'><b>Error:</b> {escape(message)}</div>"
        )
        print(f"[UI TEXT APPENDED] ai=error {message[:120]!r}", flush=True)
