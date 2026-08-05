"""
Frameless stealth overlay dashboard — dialogue + AI stream routing.

Split-view:
  TOP    — Live Conversation Stream (interviewer left / candidate right)
  BOTTOM — AI Copilot Core Guidance (QTextBrowser Markdown stream)

All text arrives via StreamHub pyqtSignals with QueuedConnection so worker
threads never touch Qt widgets directly.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QMouseEvent, QPalette
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.config import CONFIG, save_config
from src.core.context import CTX
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger
from src.core.stream_hub import StreamHub
from src.data.database import get_db
from src.services.ai_orchestrator import AIOrchestrator
from src.services.audio_service import AudioCaptureService
from src.services.ocr_service import OCRRegionService
from src.services.rag_service import RAGManager
from src.services.stealth_service import StealthService
from src.ui.dialogue_widgets import AIGuidanceBrowser, LiveConversationFeed
from src.ui.snipping_widget import SnippingWidget
from src.ui.styles import STYLESHEET

log = get_logger("ui")


class OverlayDashboard(QWidget):
    def __init__(
        self,
        audio: AudioCaptureService,
        ocr: OCRRegionService,
        ai: AIOrchestrator,
        rag: RAGManager,
        stealth: StealthService,
        hub: StreamHub,
    ) -> None:
        super().__init__(None)
        self.audio = audio
        self.ocr = ocr
        self.ai = ai
        self.rag = rag
        self.stealth = stealth
        self.hub = hub

        self._drag_pos = None
        self._snip = SnippingWidget()
        self._snip.regionSelected.connect(self._on_region_selected)
        # Auto-ask is owned by AIOrchestrator (triggers on interviewer prompts,
        # not only '?'). Do NOT also fire ask_ai here — that caused races.

        self._build_ui()
        self.ai.set_mode_provider(lambda: self.mode.currentText())
        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("Interview Copilot")
        self.resize(max(CONFIG.ui.width, 520), max(CONFIG.ui.height, 900))

        CTX.opacity = CONFIG.ui.opacity
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
        self._wire_stream_hub()

        QTimer.singleShot(200, self._init_native)

        session = get_db().create_session(title="Live Interview")
        CTX.set_session(session.id)
        BUS.publish(EventType.SESSION_STARTED, session_id=session.id)
        print("[UI TEXT APPENDED] dashboard ready — waiting for streams", flush=True)

    # ---- stream wiring (CRITICAL) ----

    def _wire_stream_hub(self) -> None:
        """
        Connect hub signals → GUI slots with QueuedConnection so emits from
        Whisper/Gemini worker threads are marshalled onto the Qt main thread.
        """
        from PyQt6.QtCore import Qt as _Qt

        queued = _Qt.ConnectionType.QueuedConnection
        self.hub.interviewer_text.connect(self._on_interviewer_text, queued)
        self.hub.candidate_text.connect(self._on_candidate_text, queued)
        self.hub.ai_started.connect(self._on_ai_started, queued)
        self.hub.ai_chunk.connect(self._on_ai_chunk, queued)
        self.hub.ai_complete.connect(self._on_ai_complete, queued)
        self.hub.ai_error.connect(self._on_ai_error, queued)
        self.hub.ocr_text.connect(self._on_ocr_text, queued)
        self.hub.status.connect(self._on_status, queued)
        # Intent classifier → dropdown (may emit from worker/timer thread)
        self.ai.mode_changed_signal.connect(self._on_mode_changed, queued)
        print("[UI ROUTE] StreamHub signals connected (QueuedConnection)", flush=True)

    # ---- window chrome ----

    def _apply_window_flags(self) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
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
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self._clear_feeds)
        for b in (
            self.btn_listen,
            self.btn_snip,
            self.btn_ocr,
            self.btn_stealth,
            self.btn_docs,
            self.btn_clear,
        ):
            ctrl.addWidget(b)
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

        # ===== SPLIT VIEW: TOP conversation (30%) / BOTTOM AI (70%) =====
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        self._main_splitter = splitter

        self.conversation = LiveConversationFeed()
        self.conversation.setMinimumHeight(120)
        self.conversation.setMaximumHeight(280)

        self.ai_view = AIGuidanceBrowser()
        self.ai_view.setMinimumHeight(450)

        top_wrap = QWidget()
        top_l = QVBoxLayout(top_wrap)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.addWidget(self.conversation)

        bottom_wrap = QWidget()
        bottom_l = QVBoxLayout(bottom_wrap)
        bottom_l.setContentsMargins(0, 0, 0, 0)
        bottom_l.addWidget(self.ai_view)

        splitter.addWidget(top_wrap)
        splitter.addWidget(bottom_wrap)
        # Favor AI guidance viewport for dense Markdown / C++ answers
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes([220, 520])
        shell_layout.addWidget(splitter, 1)

        # OCR peek (compact)
        self.ocr_view = QTextEdit()
        self.ocr_view.setReadOnly(True)
        self.ocr_view.setMaximumHeight(48)
        self.ocr_view.setPlaceholderText("OCR region text (optional)…")
        shell_layout.addWidget(self.ocr_view)

        # Pipeline / API diagnostic log (shows Groq/Gemini failures in-UI)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(56)
        self.log_view.setPlaceholderText("Pipeline log — API errors and diarization notes appear here…")
        self.log_view.setStyleSheet(
            "QTextEdit{background:#140E0E;color:#FFB4B4;border:1px solid #4A3030;"
            "border-radius:6px;font-size:11px;}"
        )
        shell_layout.addWidget(self.log_view)

        # Ask row — mode auto-updates from intent classifier
        ask_row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(
            ["auto", "coding", "technical_discussion", "behavioral", "debug"]
        )
        self.mode.setToolTip(
            "Auto-switches from interviewer speech (coding / technical discussion / behavioral)"
        )
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

        hint = QLabel(
            "Hotkeys: Alt+H hide · Alt+S snip · Alt+Enter ask  |  "
            "Blue = Interviewer · Grey = You · Bottom = AI  |  "
            "Mode auto-switches from speech · AI re-asks on new interviewer prompts"
        )
        hint.setObjectName("StatusLabel")
        shell_layout.addWidget(hint)

        root.addWidget(shell)

    # ---- stream slots (GUI thread only) ----

    @pyqtSlot(str)
    def _on_mode_changed(self, mode: str) -> None:
        """Sync Ask-mode dropdown when intent classifier fires."""
        mode = (mode or "").strip()
        if not mode or not hasattr(self, "mode"):
            return
        idx = self.mode.findText(mode)
        if idx < 0 and mode == "system_design":
            idx = self.mode.findText("technical_discussion")
            mode = "technical_discussion"
        if idx < 0:
            return
        if self.mode.currentIndex() != idx:
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(idx)
            self.mode.blockSignals(False)
            self._append_log(f"MODE auto → {mode}")
            print(f"[UI ROUTE] mode dropdown → {mode}", flush=True)

    @pyqtSlot(str)
    def _on_interviewer_text(self, text: str) -> None:
        print(f"[UI TEXT APPENDED] ← interviewer slot text={text[:100]!r}", flush=True)
        self.conversation.append_interviewer(text)
        self._persist_transcript("interviewer", text)
        self._append_log(f"INTERVIEWER: {text[:120]}")
        # Auto Gemini trigger lives in AIOrchestrator.record_interviewer()

    @pyqtSlot(str)
    def _on_candidate_text(self, text: str) -> None:
        print(f"[UI TEXT APPENDED] ← candidate slot text={text[:100]!r}", flush=True)
        self.conversation.append_candidate(text)
        self._persist_transcript("candidate", text)
        self._append_log(f"CANDIDATE: {text[:120]}")

    @pyqtSlot()
    def _on_ai_started(self) -> None:
        print("[UI TEXT APPENDED] ← ai_started", flush=True)
        self.ai_view.begin_stream()
        self._on_status("Thinking…")
        self._append_log("GEMINI stream started")

    @pyqtSlot(str)
    def _on_ai_chunk(self, chunk: str) -> None:
        self.ai_view.append_chunk(chunk)

    @pyqtSlot(str, float)
    def _on_ai_complete(self, text: str, latency_ms: float) -> None:
        self.ai_view.finalize(text or None)
        self._on_status(f"Answer ready ({latency_ms:.0f} ms)")
        self._append_log(f"GEMINI complete ({latency_ms:.0f} ms, {len(text or '')} chars)")

    @pyqtSlot(str)
    def _on_ai_error(self, message: str) -> None:
        self.ai_view.show_error(message)
        self._on_status(message)
        self._append_log(f"ERROR: {message}")

    def _append_log(self, line: str) -> None:
        if not hasattr(self, "log_view"):
            return
        from datetime import datetime

        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"{stamp}  {line}")
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    @pyqtSlot(str)
    def _on_ocr_text(self, text: str) -> None:
        self.ocr_view.setPlainText(text)

    @pyqtSlot(str)
    def _on_status(self, message: str) -> None:
        self.status.setText(message)
        CTX.set_status(message)
        # Surface pipeline/device errors in the diagnostic strip too
        lower = (message or "").lower()
        if any(k in lower for k in ("error", "fail", "missing", "unavailable", "no microphone", "no wasapi")):
            self._append_log(message)

    def _persist_transcript(self, speaker: str, text: str) -> None:
        if not CTX.session_id:
            return
        try:
            get_db().add_message(
                CTX.session_id,
                role="transcript",
                content=text,
                speaker=speaker,
                source="audio",
            )
        except Exception:  # noqa: BLE001
            log.exception("Failed to persist transcript")

    def _clear_feeds(self) -> None:
        self.conversation.clear()
        self.ai_view.begin_stream()
        self.ai_view.browser.clear()
        if hasattr(self, "log_view"):
            self.log_view.clear()
        try:
            self.ai.memory.clear()
        except Exception:  # noqa: BLE001
            pass
        print("[UI TEXT APPENDED] feeds cleared", flush=True)

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
                    info = self.rag.ingest_file(path)
                    self._on_status(f"Indexed {info.get('filename')} ({info.get('chunks')} chunks)")
                except Exception as exc:  # noqa: BLE001
                    self._on_status(f"Ingest failed: {exc}")
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
            print("[AUDIO CAPTURED] Listen button → STOP", flush=True)
            self.audio.stop()
            self.btn_listen.setText("Listen")
            self._on_status("Audio stopped")
        else:
            print("[AUDIO CAPTURED] Listen button → START", flush=True)
            self._append_log("Listen clicked — starting mic + WASAPI loopback")
            self.audio.start()
            if self.audio.running:
                self.btn_listen.setText("Stop")
                self._on_status("Listening…")
            else:
                self.btn_listen.setText("Listen")
                self._on_status("Listen failed — see pipeline log / console")
                self._append_log("Listen failed to start — check GROQ_API_KEY and audio devices")

    def start_snip(self) -> None:
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
        text = self.ocr.capture_once()
        if text:
            self.ocr_view.setPlainText(text)
            if self.hub:
                self.hub.ocr_text.emit(text)

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
        self.show()
        self.raise_()
        self.activateWindow()
        if enabled:
            self.opacity_slider.setEnabled(False)
            self._on_status("Stealth ON — hidden from screen share (still visible to you)")
        else:
            self.opacity_slider.setEnabled(True)
            self.setWindowOpacity(CTX.opacity)
            self._on_status("Stealth OFF — visible in screen share")

    def _sync_stealth_button(self) -> None:
        on = CTX.stealth_enabled
        self.btn_stealth.setText("Stealth: ON" if on else "Stealth: OFF")
        if hasattr(self, "opacity_slider"):
            self.opacity_slider.setEnabled(not on)

    def restore_overlay(self) -> None:
        self.stealth.reveal(self)
        CONFIG.ui.stealth_enabled = False
        save_config(CONFIG)
        self._sync_stealth_button()
        self.show()
        self.raise_()
        self.activateWindow()
        CTX.overlay_visible = True
        self._on_status("Overlay restored (Stealth OFF)")

    def pick_documents(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Upload resume / JD / notes",
            "",
            "Documents (*.pdf *.txt *.md *.docx *.json);;All Files (*)",
        )
        for f in files:
            try:
                info = self.rag.ingest_file(f)
                self._on_status(f"Indexed {info.get('filename')} ({info.get('chunks')} chunks)")
            except Exception as exc:  # noqa: BLE001
                self._on_status(f"Ingest failed: {exc}")

    def ask_ai(self) -> None:
        hint = self.input.text().strip()
        mode = self.mode.currentText()
        # Manual Ask still runs local intent on transcript window if mode is auto
        if mode == "auto":
            try:
                from src.services.ai_orchestrator import classify_intent

                window = self.ai.memory.interviewer_window(3)
                classified = classify_intent(window or hint)
                if classified != "auto":
                    mode = classified
                    self._on_mode_changed(mode)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.rag.refresh_context_from_latest()
        except Exception:  # noqa: BLE001
            log.exception("RAG refresh failed")

        rolling = getattr(self.audio, "rolling_context", "") or CTX.transcript_block(40)
        print(f"[GEMINI STREAM START] ask_ai mode={mode} rolling_chars={len(rolling)}", flush=True)
        self.ai.ask(
            user_hint=hint
            or (
                "Answer the LATEST substantive [INTERVIEWER] request now. "
                "Ignore prior audio checks."
            ),
            mode=mode,
            include_image=True,
            persist=True,
            rolling_context=rolling,
        )
        if hint and CTX.session_id:
            get_db().add_message(CTX.session_id, role="user", content=hint, source="manual")
        self.input.clear()

    def _on_opacity(self, value: int) -> None:
        opacity = value / 100.0
        self.stealth.set_opacity(opacity)
        CONFIG.ui.opacity = opacity
        if not hasattr(self, "_opacity_timer"):
            self._opacity_timer = QTimer(self)
            self._opacity_timer.setSingleShot(True)
            self._opacity_timer.timeout.connect(lambda: save_config(CONFIG))
        self._opacity_timer.start(500)

    def handle_hotkey(self, action: str) -> None:
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
