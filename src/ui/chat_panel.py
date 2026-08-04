"""Chat / transcript panel widgets with interviewer vs candidate bubbles."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class Bubble(QLabel):
    def __init__(self, text: str, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        object_map = {
            "interviewer": "BubbleInterviewer",
            "candidate": "BubbleCandidate",
            "assistant": "BubbleAssistant",
            "user": "BubbleCandidate",
        }
        self.setObjectName(object_map.get(kind, "BubbleAssistant"))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)


class ChatPanel(QWidget):
    """Scrollable multi-speaker conversation view."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(6)
        self._scroll.setWidget(self._container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._scroll)

        self._mono = QFont("Cascadia Code", 10)
        if not self._mono.exactMatch():
            self._mono = QFont("Consolas", 10)

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def add_message(self, speaker: str, text: str, mono: bool = False) -> None:
        header = QLabel(speaker.upper())
        header.setStyleSheet("color: #7F92B0; font-size: 10px; font-weight: 600;")
        bubble = Bubble(text, speaker)
        if mono:
            bubble.setFont(self._mono)
        row = QVBoxLayout()
        row.setSpacing(2)
        wrap = QWidget()
        wrap.setLayout(row)
        row.addWidget(header)
        row.addWidget(bubble)
        self._layout.addWidget(wrap)
        self._scroll_to_bottom()

    def update_last_assistant(self, text: str) -> None:
        """Replace/update the last assistant bubble during streaming."""
        count = self._layout.count()
        if count == 0:
            self.add_message("assistant", text, mono=True)
            return
        wrap = self._layout.itemAt(count - 1).widget()
        if wrap is None:
            self.add_message("assistant", text, mono=True)
            return
        labels = wrap.findChildren(QLabel)
        if len(labels) >= 2 and labels[0].text() == "ASSISTANT":
            labels[1].setText(text)
            self._scroll_to_bottom()
        else:
            self.add_message("assistant", text, mono=True)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())


class SplitTranscriptPanel(QWidget):
    """Side-by-side interviewer / candidate live transcript columns."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.interviewer = ChatPanel()
        self.candidate = ChatPanel()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        left = QVBoxLayout()
        left_l = QLabel("Interviewer")
        left_l.setObjectName("TitleLabel")
        left.addWidget(left_l)
        left.addWidget(self.interviewer)

        right = QVBoxLayout()
        right_l = QLabel("You")
        right_l.setObjectName("TitleLabel")
        right.addWidget(right_l)
        right.addWidget(self.candidate)

        layout.addLayout(left, 1)
        layout.addLayout(right, 1)

    def add_transcript(self, speaker: str, text: str) -> None:
        if speaker == "interviewer":
            self.interviewer.add_message("interviewer", text)
        else:
            self.candidate.add_message("candidate", text)
