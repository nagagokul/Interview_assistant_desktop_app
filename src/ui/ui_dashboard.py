"""
Frameless stealth overlay dashboard.

- Borderless dark window with transparency slider
- Split interviewer / candidate transcript panels
- Assistant streaming output
- Drag-and-drop document ingest for RAG
- Native WDA_EXCLUDEFROMCAPTURE screen-share exclusion
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QMouseEvent, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.config import CONFIG, save_config
from src.core.context import CTX
from src.core.event_bus import BUS, Event, EventType
from src.core.logging_setup import get_logger
from src.data.database import get_db
from src.services.ai_orchestrator import AIOrchestrator
from src.services.audio_service import AudioCaptureService
from src.services.ocr_service import OCRRegionService
from src.services.rag_service import RAGManager
from src.services.stealth_service import StealthService
from src.ui.chat_panel import ChatPanel, SplitTranscriptPanel
from src.ui.snipping_widget import SnippingWidget
from src.ui.styles import STYLESHEET

log = get_logger("ui")


class _AsyncBridge(QThread):
    """Marshals EventBus callbacks onto the Qt GUI thread via queued signals."""

    transcript = pyqtSignal(str, str)
    ocr_text = pyqtSignal(str)
    ai_token = pyqtSignal(str)
    ai_complete = pyqtSignal(str, float)
    ai_error = pyqtSignal(str)
    status = pyqtSignal(str)
    document_indexed = pyqtSignal(str, int)
    hotkey = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._running = True

    def start_bridge(self) -> None:
        BUS.subscribe(EventType.TRANSCRIPT, self._on_transcript)
        BUS.subscribe(EventType.OCR_TEXT, self._on_ocr)
        BUS.subscribe(EventType.AI_TOKEN, self._on_token)
        BUS.subscribe(EventType.AI_COMPLETE, self._on_complete)
        BUS.subscribe(EventType.AI_ERROR, self._on_error)
        BUS.subscribe(EventType.STATUS, self._on_status)
        BUS.subscribe(EventType.DOCUMENT_INDEXED, self._on_doc)
        BUS.subscribe(EventType.HOTKEY, self._on_hotkey)

    def _on_transcript(self, event: Event) -> None:
        self.transcript.emit(event.payload.get("speaker", ""), event.payload.get("text", ""))

    def _on_ocr(self, event: Event) -> None:
        self.ocr_text.emit(event.payload.get("text", ""))

    def _on_token(self, event: Event) -> None:
        self.ai_token.emit(event.payload.get("token", ""))

    def _on_complete(self, event: Event) -> None:
        self.ai_complete.emit(event.payload.get("text", ""), float(event.payload.get("latency_ms", 0)))

    def _on_error(self, event: Event) -> None:
        self.ai_error.emit(event.payload.get("message", "error"))

    def _on_status(self, event: Event) -> None:
        self.status.emit(event.payload.get("message", ""))

    def _on_doc(self, event: Event) -> None:
        self.document_indexed.emit(event.payload.get("filename", ""), int(event.payload.get("chunks", 0)))

    def _on_hotkey(self, event: Event) -> None:
        self.hotkey.emit(event.payload.get("action", ""))


class OverlayDashboard(QWidget):
    def __init__(
        self,
        audio: AudioCaptureService,
        ocr: OCRRegionService,
        ai: AIOrchestrator,
        rag: RAGManager,
        stealth: StealthService,
    ) -> None:
        super().__init__(None)
        self.audio = audio
        self.ocr = ocr
        self.ai = ai
        self.rag = rag
        self.stealth = stealth

        self._drag_pos = None
        self._assistant_buffer = ""
        self._snip = SnippingWidget()
        self._snip.regionSelected.connect(self._on_region_selected)

        self._bridge = _AsyncBridge()
        self._bridge.start_bridge()
        self._bridge.transcript.connect(self._ui_transcript)
        self._bridge.ocr_text.connect(self._ui_ocr)
        self._bridge.ai_token.connect(self._ui_token)
        self._bridge.ai_complete.connect(self._ui_complete)
        self._bridge.ai_error.connect(self._ui_error)
        self._bridge.status.connect(self._ui_status)
        self._bridge.document_indexed.connect(self._ui_doc)
        self._bridge.hotkey.connect(self._ui_hotkey)

        self._build_ui()
        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("Interview Copilot")
        self.resize(CONFIG.ui.width, CONFIG.ui.height)
        CTX.opacity = CONFIG.ui.opacity
        # Prefer visible-first on every cold start. Older builds saved stealth=true
        # together with Win32 layered alpha, which hid the overlay on some PCs.
        # Users turn stealth on explicitly from the UI when ready for a call.
        if CONFIG.ui.stealth_enabled:
            log.warning("Resetting saved stealth=true → false for safe startup visibility")
            CONFIG.ui.stealth_enabled = False
            try:
                save_config(CONFIG)
            except Exception:
                pass
        CTX.stealth_enabled = False
        self.setWindowOpacity(CONFIG.ui.opacity)
        self._sync_stealth_button()

        self.setAcceptDrops(True)
        self._apply_window_flags()

        # Apply stealth after the native window handle exists
        QTimer.singleShot(200, self._init_native)

        # Start encrypted history session
        session = get_db().create_session(title="Live Interview")
        CTX.set_session(session.id)
        BUS.publish(EventType.SESSION_STARTED, session_id=session.id)

    # ---- window chrome ----

    def _apply_window_flags(self) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        # Solid fill — translucent root + Win32 layered alpha + stealth affinity
        # made the whole overlay disappear on some Windows builds.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(18, 22, 28))
        self.setPalette(palette)

    def _init_native(self) -> None:
        self.stealth.register(self)
        self.stealth.apply_to(self, CTX.stealth_enabled)
        self.show()
        self.raise_()
        self._sync_stealth_button()
        log.info("Overlay native HWND ready; stealth=%s", CTX.stealth_enabled)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)

        shell = QWidget()
        shell.setObjectName("OverlayRoot")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(10, 8, 10, 10)
        shell_layout.setSpacing(6)

        # Title bar
        title_row = QHBoxLayout()
        self.title = QLabel("Interview Copilot")
        self.title.setObjectName("TitleLabel")
        self.status = QLabel("Ready")
        self.status.setObjectName("StatusLabel")
        self.btn_hide = QPushButton("Hide")
        self.btn_hide.clicked.connect(self.toggle_visibility)
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedWidth(28)
        self.btn_close.clicked.connect(self._minimize_to_tray)
        title_row.addWidget(self.title)
        title_row.addStretch()
        title_row.addWidget(self.status)
        title_row.addWidget(self.btn_hide)
        title_row.addWidget(self.btn_close)
        shell_layout.addLayout(title_row)

        # Controls
        ctrl = QHBoxLayout()
        self.btn_listen = QPushButton("Listen")
        self.btn_listen.setObjectName("PrimaryButton")
        self.btn_listen.clicked.connect(self.toggle_listen)
        self.btn_snip = QPushButton("OCR Region")
        self.btn_snip.clicked.connect(self.start_snip)
        self.btn_ocr = QPushButton("OCR Watch")
        self.btn_ocr.clicked.connect(self.toggle_ocr)
        self.btn_stealth = QPushButton("Stealth: OFF")
        self.btn_stealth.clicked.connect(self.toggle_stealth)
        self.btn_docs = QPushButton("Docs")
        self.btn_docs.clicked.connect(self.pick_documents)
        ctrl.addWidget(self.btn_listen)
        ctrl.addWidget(self.btn_snip)
        ctrl.addWidget(self.btn_ocr)
        ctrl.addWidget(self.btn_stealth)
        ctrl.addWidget(self.btn_docs)
        shell_layout.addLayout(ctrl)

        # Opacity
        op = QHBoxLayout()
        op.addWidget(QLabel("Opacity"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(25, 100)
        self.opacity_slider.setValue(int(CONFIG.ui.opacity * 100))
        self.opacity_slider.valueChanged.connect(self._on_opacity)
        op.addWidget(self.opacity_slider)
        shell_layout.addLayout(op)

        # Tabs
        self.tabs = QTabWidget()
        self.split = SplitTranscriptPanel()
        self.assistant = ChatPanel()
        self.ocr_view = QTextEdit()
        self.ocr_view.setReadOnly(True)
        self.ocr_view.setPlaceholderText("OCR output from selected screen region…")
        self.tabs.addTab(self.split, "Live")
        self.tabs.addTab(self.assistant, "Assistant")
        self.tabs.addTab(self.ocr_view, "OCR")
        shell_layout.addWidget(self.tabs, 1)

        # Ask row
        ask_row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["auto", "coding", "system_design", "behavioral", "debug"])
        self.input = QLineEdit()
        self.input.setPlaceholderText("Hint or question… (Alt+Enter)")
        self.input.returnPressed.connect(self.ask_ai)
        self.btn_ask = QPushButton("Ask")
        self.btn_ask.setObjectName("PrimaryButton")
        self.btn_ask.clicked.connect(self.ask_ai)
        ask_row.addWidget(self.mode)
        ask_row.addWidget(self.input, 1)
        ask_row.addWidget(self.btn_ask)
        shell_layout.addLayout(ask_row)

        hint = QLabel("Hotkeys: Alt+H hide · Alt+S snip · Alt+Enter ask  |  Drop resume/JD/PDF here")
        hint.setObjectName("StatusLabel")
        shell_layout.addWidget(hint)

        root.addWidget(shell)

    # ---- interactions ----

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                try:
                    self.rag.ingest_file(path)
                except Exception as exc:  # noqa: BLE001
                    self._ui_status(f"Ingest failed: {exc}")
                    log.exception("Drop ingest failed")

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
            CTX.overlay_visible = False
        else:
            self.show()
            self.raise_()
            CTX.overlay_visible = True
            QTimer.singleShot(50, self._reapply_stealth_safe)

    def _reapply_stealth_safe(self) -> None:
        self.stealth.apply_to(self, CTX.stealth_enabled)
        self.show()
        self.raise_()
        self._sync_stealth_button()

    def _minimize_to_tray(self) -> None:
        self.hide()
        CTX.overlay_visible = False

    def toggle_listen(self) -> None:
        if self.audio.running:
            self.audio.stop()
            self.btn_listen.setText("Listen")
        else:
            self.audio.start()
            self.btn_listen.setText("Stop")

    def start_snip(self) -> None:
        # Temporarily hide overlay so it isn't in the selection
        was_visible = self.isVisible()
        if was_visible:
            self.hide()
        self._snip_restore = was_visible
        self._snip.begin()

    @pyqtSlot(int, int, int, int)
    def _on_region_selected(self, left: int, top: int, right: int, bottom: int) -> None:
        self.ocr.set_region((left, top, right, bottom))
        if getattr(self, "_snip_restore", True):
            self.show()
            QTimer.singleShot(50, self._reapply_stealth_safe)
        # Immediate OCR pass
        text = self.ocr.capture_once()
        if text:
            self.ocr_view.setPlainText(text)

    def toggle_ocr(self) -> None:
        if self.ocr.running:
            self.ocr.stop()
            self.btn_ocr.setText("OCR Watch")
        else:
            self.ocr.start()
            self.btn_ocr.setText("OCR Stop")

    def toggle_stealth(self) -> None:
        enabled = not CTX.stealth_enabled
        self.stealth.apply_all(enabled)
        CONFIG.ui.stealth_enabled = enabled
        save_config(CONFIG)
        self._sync_stealth_button()
        # Always force local visibility after toggling
        self.show()
        self.raise_()
        self.activateWindow()
        if enabled:
            # Opacity slider is ignored while stealth is on (Win32 alpha conflict)
            self.opacity_slider.setEnabled(False)
            self._ui_status("Stealth ON — hidden from screen share (still visible to you)")
        else:
            self.opacity_slider.setEnabled(True)
            self.setWindowOpacity(CTX.opacity)
            self._ui_status("Stealth OFF — visible in screen share")

    def _sync_stealth_button(self) -> None:
        on = CTX.stealth_enabled
        self.btn_stealth.setText("Stealth: ON" if on else "Stealth: OFF")
        if hasattr(self, "opacity_slider"):
            self.opacity_slider.setEnabled(not on)

    def restore_overlay(self) -> None:
        """Tray / recovery: show overlay and turn stealth off if needed."""
        self.stealth.reveal(self)
        CONFIG.ui.stealth_enabled = False
        save_config(CONFIG)
        self._sync_stealth_button()
        self.show()
        self.raise_()
        self.activateWindow()
        CTX.overlay_visible = True
        self._ui_status("Overlay restored (Stealth OFF)")

    def pick_documents(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Upload resume / JD / notes",
            "",
            "Documents (*.pdf *.txt *.md *.docx *.json);;All Files (*)",
        )
        for f in files:
            try:
                self.rag.ingest_file(f)
            except Exception as exc:  # noqa: BLE001
                self._ui_status(f"Ingest failed: {exc}")

    def ask_ai(self) -> None:
        hint = self.input.text().strip()
        mode = self.mode.currentText()
        self._assistant_buffer = ""
        self.assistant.add_message("assistant", "…", mono=True)
        self.tabs.setCurrentWidget(self.assistant)
        # Refresh RAG with latest conversational context
        try:
            self.rag.refresh_context_from_latest()
        except Exception:  # noqa: BLE001
            log.exception("RAG refresh failed")
        self.ai.ask(user_hint=hint, mode=mode, include_image=True, persist=True)
        if hint:
            get_db().add_message(CTX.session_id or "", role="user", content=hint, source="manual")
        self.input.clear()

    def _on_opacity(self, value: int) -> None:
        opacity = value / 100.0
        self.stealth.set_opacity(opacity)
        CONFIG.ui.opacity = opacity
        # Debounced save
        if not hasattr(self, "_opacity_timer"):
            self._opacity_timer = QTimer(self)
            self._opacity_timer.setSingleShot(True)
            self._opacity_timer.timeout.connect(lambda: save_config(CONFIG))
        self._opacity_timer.start(500)

    # ---- EventBus → UI slots ----

    @pyqtSlot(str, str)
    def _ui_transcript(self, speaker: str, text: str) -> None:
        self.split.add_transcript(speaker, text)
        if CTX.session_id:
            get_db().add_message(
                CTX.session_id,
                role="transcript",
                content=text,
                speaker=speaker,
                source="audio",
            )
        # Auto-ask on interviewer questions ending with '?'
        if speaker == "interviewer" and text.strip().endswith("?"):
            if not CTX.is_ai_streaming:
                self.mode.setCurrentText("auto")
                self.ask_ai()

    @pyqtSlot(str)
    def _ui_ocr(self, text: str) -> None:
        self.ocr_view.setPlainText(text)

    @pyqtSlot(str)
    def _ui_token(self, token: str) -> None:
        self._assistant_buffer += token
        self.assistant.update_last_assistant(self._assistant_buffer)
        # Keep under 30ms perceived latency — Qt paints on next event loop tick

    @pyqtSlot(str, float)
    def _ui_complete(self, text: str, latency_ms: float) -> None:
        if text:
            self.assistant.update_last_assistant(text)
        self._ui_status(f"Answer ready ({latency_ms:.0f} ms)")

    @pyqtSlot(str)
    def _ui_error(self, message: str) -> None:
        self.assistant.add_message("assistant", f"Error: {message}")
        self._ui_status(message)

    @pyqtSlot(str)
    def _ui_status(self, message: str) -> None:
        self.status.setText(message)
        CTX.set_status(message)

    @pyqtSlot(str, int)
    def _ui_doc(self, filename: str, chunks: int) -> None:
        self._ui_status(f"Indexed {filename} ({chunks} chunks)")

    @pyqtSlot(str)
    def _ui_hotkey(self, action: str) -> None:
        if action == "toggle_overlay":
            self.toggle_visibility()
        elif action == "snip_region":
            self.start_snip()
        elif action == "ask_ai":
            self.ask_ai()

    def shutdown(self) -> None:
        try:
            if self.audio.running:
                self.audio.stop()
            if self.ocr.running:
                self.ocr.stop()
            self.ai.cancel()
            if CTX.session_id:
                get_db().end_session(CTX.session_id)
        except Exception:  # noqa: BLE001
            log.exception("Shutdown error")
