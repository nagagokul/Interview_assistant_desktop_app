"""Dark, frameless overlay stylesheet — low clutter, high contrast."""

from __future__ import annotations

STYLESHEET = """
* {
    font-family: "Segoe UI", "Cascadia Code", "Consolas", sans-serif;
    color: #E8ECF1;
}

QWidget#OverlayRoot {
    background-color: #12161C;
    border: 1px solid rgba(90, 110, 140, 120);
    border-radius: 10px;
}

QLabel#TitleLabel {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.4px;
    color: #A8C1FF;
    padding: 4px 2px;
}

QLabel#StatusLabel {
    font-size: 11px;
    color: #8B9BB4;
    padding: 2px;
}

QPushButton {
    background-color: rgba(40, 52, 72, 200);
    border: 1px solid rgba(100, 120, 150, 90);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    min-height: 26px;
}

QPushButton:hover {
    background-color: rgba(55, 72, 100, 220);
}

QPushButton:pressed {
    background-color: rgba(30, 40, 58, 220);
}

QPushButton#DangerButton {
    background-color: rgba(90, 36, 42, 200);
}

QPushButton#PrimaryButton {
    background-color: rgba(36, 70, 120, 220);
    border-color: rgba(120, 160, 220, 120);
}

QSlider::groove:horizontal {
    height: 4px;
    background: rgba(70, 85, 110, 160);
    border-radius: 2px;
}

QSlider::handle:horizontal {
    width: 12px;
    height: 12px;
    margin: -4px 0;
    background: #7BA3FF;
    border-radius: 6px;
}

QTextEdit, QPlainTextEdit {
    background-color: rgba(12, 16, 22, 200);
    border: 1px solid rgba(70, 85, 110, 100);
    border-radius: 6px;
    padding: 6px;
    font-size: 12px;
    selection-background-color: #2F4F7A;
}

QLineEdit {
    background-color: rgba(12, 16, 22, 200);
    border: 1px solid rgba(70, 85, 110, 100);
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 12px;
}

QTabWidget::pane {
    border: 1px solid rgba(70, 85, 110, 80);
    border-radius: 6px;
    background: transparent;
}

QTabBar::tab {
    background: rgba(28, 36, 48, 180);
    border: 1px solid rgba(70, 85, 110, 80);
    padding: 6px 12px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-size: 11px;
}

QTabBar::tab:selected {
    background: rgba(40, 58, 88, 220);
    color: #D6E4FF;
}

QScrollBar:vertical {
    width: 8px;
    background: transparent;
}

QScrollBar::handle:vertical {
    background: rgba(90, 110, 140, 140);
    border-radius: 4px;
    min-height: 24px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QLabel#BubbleInterviewer {
    background-color: rgba(55, 40, 70, 180);
    border-radius: 8px;
    padding: 8px;
    font-size: 12px;
}

QLabel#BubbleCandidate {
    background-color: rgba(30, 55, 70, 180);
    border-radius: 8px;
    padding: 8px;
    font-size: 12px;
}

QLabel#BubbleAssistant {
    background-color: rgba(28, 48, 80, 200);
    border-radius: 8px;
    padding: 8px;
    font-size: 12px;
}

QComboBox {
    background-color: rgba(12, 16, 22, 200);
    border: 1px solid rgba(70, 85, 110, 100);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
}

QComboBox QAbstractItemView {
    background-color: #1A2230;
    selection-background-color: #2F4F7A;
}
"""
