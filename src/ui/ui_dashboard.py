"""
Frameless stealth overlay dashboard — dialogue + AI stream routing.

Split-view:
  TOP    — Live Conversation Stream (interviewer left / candidate right)
  BOTTOM — AI Copilot Core Guidance (QTextBrowser Markdown stream)

All text arrives via StreamHub pyqtSignals with QueuedConnection so worker
threads never touch Qt widgets directly.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QMouseEvent, QPalette, QResizeEvent
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

from src.core.api_usage import USAGE
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

_RESIZE_MARGIN = 8
_COLLAPSED_HEIGHT = 42
_MIN_WIDTH = 360
_MIN_HEIGHT = 420


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
        self._resize_edge: str | None = None
        self._resize_origin: QPoint | None = None
        self._resize_geom: QRect | None = None
        self._collapsed = False
        self._expanded_size = QSize(max(CONFIG.ui.width, 520), max(CONFIG.ui.height, 900))
        self._body_widgets: list[QWidget] = []
        self._snip = SnippingWidget()
        self._snip.regionSelected.connect(self._on_region_selected)
        # Auto-ask is owned by AIOrchestrator (triggers on interviewer prompts,
        # not only '?'). Do NOT also fire ask_ai here — that caused races.

        self._build_ui()
        self.ai.set_mode_provider(lambda: self.mode.currentText())
        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("Interview Copilot")
        self.resize(self._expanded_size)
        self.setMinimumSize(_MIN_WIDTH, _COLLAPSED_HEIGHT)

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
        self.setMouseTracking(True)
        self._apply_window_flags()
        self._wire_stream_hub()
        self._refresh_usage_labels()

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
        self.hub.api_usage.connect(self._on_api_usage, queued)
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
        self.btn_hide.setToolTip("Hide overlay (Alt+H) — restore from tray or hotkey")
        self.btn_hide.clicked.connect(self.toggle_visibility)
        self.btn_min = QPushButton("−")
        self.btn_min.setObjectName("ChromeButton")
        self.btn_min.setFixedWidth(28)
        self.btn_min.setToolTip("Minimize to title bar — click □ to expand again")
        self.btn_min.clicked.connect(self._toggle_collapsed)
        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("ChromeButton")
        self.btn_close.setFixedWidth(28)
        self.btn_close.setToolTip("Send to system tray")
        self.btn_close.clicked.connect(self._minimize_to_tray)
        title_row.addWidget(self.title)
        title_row.addStretch()
        title_row.addWidget(self.status)
        title_row.addWidget(self.btn_hide)
        title_row.addWidget(self.btn_min)
        title_row.addWidget(self.btn_close)
        shell_layout.addLayout(title_row)

        # API free-tier usage meters (Groq STT + Gemini AI)
        self._usage_host = QWidget()
        usage_row = QHBoxLayout(self._usage_host)
        usage_row.setContentsMargins(0, 0, 0, 0)
        self.usage_groq = QLabel("Groq STT: —")
        self.usage_groq.setObjectName("UsageOk")
        self.usage_groq.setToolTip("Groq Whisper free-tier RPM / daily usage")
        self.usage_gemini = QLabel("Gemini AI: —")
        self.usage_gemini.setObjectName("UsageOk")
        self.usage_gemini.setToolTip("Gemini free-tier RPM / daily usage")
        usage_row.addWidget(self.usage_groq, 1)
        usage_row.addWidget(self.usage_gemini, 1)
        shell_layout.addWidget(self._usage_host)

        # Controls
        self._ctrl_host = QWidget()
        ctrl = QHBoxLayout(self._ctrl_host)
        ctrl.setContentsMargins(0, 0, 0, 0)
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
        shell_layout.addWidget(self._ctrl_host)

        # Opacity
        self._op_host = QWidget()
        op = QHBoxLayout(self._op_host)
        op.setContentsMargins(0, 0, 0, 0)
        op.addWidget(QLabel("Opacity"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(25, 100)
        self.opacity_slider.setValue(int(CONFIG.ui.opacity * 100))
        self.opacity_slider.valueChanged.connect(self._on_opacity)
        op.addWidget(self.opacity_slider)
        shell_layout.addWidget(self._op_host)

        # ===== SPLIT VIEW: TOP conversation (30%) / BOTTOM AI (70%) =====
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        self._main_splitter = splitter

        self.conversation = LiveConversationFeed()
        self.conversation.setMinimumHeight(120)
        self.conversation.setMaximumHeight(280)

        self.ai_view = AIGuidanceBrowser()
        self.ai_view.setMinimumHeight(200)

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
        self._ask_host = QWidget()
        ask_row = QHBoxLayout(self._ask_host)
        ask_row.setContentsMargins(0, 0, 0, 0)
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
        shell_layout.addWidget(self._ask_host)

        self._hint = QLabel(
            "Hotkeys: Alt+H hide · Alt+S snip · Alt+Enter ask  |  "
            "− minimizes to title bar  |  drag edges to resize  |  "
            "Groq/Gemini meters warn before free-tier limits"
        )
        self._hint.setObjectName("StatusLabel")
        self._hint.setWordWrap(True)
        shell_layout.addWidget(self._hint)

        root.addWidget(shell)
        self._shell = shell

        # Widgets hidden when collapsed to title-bar strip
        self._body_widgets = [
            self._usage_host,
            self._ctrl_host,
            self._op_host,
            self._main_splitter,
            self.ocr_view,
            self.log_view,
            self._ask_host,
            self._hint,
        ]

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

    @pyqtSlot(str, str, bool, bool, int, int, int, int)
    def _on_api_usage(
        self,
        provider: str,
        message: str,
        warn: bool,
        critical: bool,
        rpm_used: int,
        rpm_limit: int,
        daily_used: int,
        daily_limit: int,
    ) -> None:
        short = f"{rpm_used}/{rpm_limit} RPM · {daily_used}/{daily_limit} today"
        if provider == "groq":
            label = self.usage_groq
            label.setText(f"Groq STT: {short}")
        else:
            label = self.usage_gemini
            label.setText(f"Gemini AI: {short}")
        if critical:
            label.setObjectName("UsageCritical")
        elif warn:
            label.setObjectName("UsageWarn")
        else:
            label.setObjectName("UsageOk")
        # Force stylesheet re-apply after objectName change
        label.style().unpolish(label)
        label.style().polish(label)
        label.setToolTip(message)
        if critical or warn:
            self._on_status(message)
            self._append_log(f"USAGE: {message}")

    def _refresh_usage_labels(self) -> None:
        for snap in USAGE.snapshots():
            self._on_api_usage(
                snap.provider,
                snap.message,
                snap.warn,
                snap.critical,
                snap.rpm_used,
                snap.rpm_limit,
                snap.daily_used,
                snap.daily_limit,
            )

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

    # ---- collapse / resize / drag ----

    def _hit_resize_edge(self, pos: QPoint) -> str | None:
        if self._collapsed:
            return None
        r = self.rect()
        m = _RESIZE_MARGIN
        left = pos.x() <= m
        right = pos.x() >= r.width() - m
        top = pos.y() <= m
        bottom = pos.y() >= r.height() - m
        if top and left:
            return "tl"
        if top and right:
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        if left:
            return "l"
        if right:
            return "r"
        if top:
            return "t"
        if bottom:
            return "b"
        return None

    def _cursor_for_edge(self, edge: str | None) -> Qt.CursorShape:
        mapping = {
            "l": Qt.CursorShape.SizeHorCursor,
            "r": Qt.CursorShape.SizeHorCursor,
            "t": Qt.CursorShape.SizeVerCursor,
            "b": Qt.CursorShape.SizeVerCursor,
            "tl": Qt.CursorShape.SizeFDiagCursor,
            "br": Qt.CursorShape.SizeFDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor,
        }
        return mapping.get(edge or "", Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            local = event.position().toPoint()
            edge = self._hit_resize_edge(local)
            if edge:
                self._resize_edge = edge
                self._resize_origin = event.globalPosition().toPoint()
                self._resize_geom = self.geometry()
                self._drag_pos = None
                event.accept()
                return
            self._resize_edge = None
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._resize_edge and self._resize_origin is not None and self._resize_geom is not None:
            delta = event.globalPosition().toPoint() - self._resize_origin
            g = QRect(self._resize_geom)
            edge = self._resize_edge
            if "l" in edge:
                g.setLeft(g.left() + delta.x())
            if "r" in edge:
                g.setRight(g.right() + delta.x())
            if "t" in edge:
                g.setTop(g.top() + delta.y())
            if "b" in edge:
                g.setBottom(g.bottom() + delta.y())
            if g.width() < _MIN_WIDTH:
                if "l" in edge:
                    g.setLeft(g.right() - _MIN_WIDTH)
                else:
                    g.setWidth(_MIN_WIDTH)
            min_h = _COLLAPSED_HEIGHT if self._collapsed else _MIN_HEIGHT
            if g.height() < min_h:
                if "t" in edge:
                    g.setTop(g.bottom() - min_h)
                else:
                    g.setHeight(min_h)
            self.setGeometry(g)
            event.accept()
            return

        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return

        edge = self._hit_resize_edge(event.position().toPoint())
        self.setCursor(self._cursor_for_edge(edge))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._resize_edge and not self._collapsed:
            self._expanded_size = self.size()
            CONFIG.ui.width = self.width()
            CONFIG.ui.height = self.height()
            try:
                save_config(CONFIG)
            except Exception:  # noqa: BLE001
                pass
        self._drag_pos = None
        self._resize_edge = None
        self._resize_origin = None
        self._resize_geom = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        # Double-click title area toggles collapsed title-bar mode
        if event.position().y() <= 40:
            self._toggle_collapsed()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._collapsed:
            self._expanded_size = self.size()

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

    def _toggle_collapsed(self) -> None:
        if self._collapsed:
            self._expand_from_titlebar()
        else:
            self._collapse_to_titlebar()

    def _collapse_to_titlebar(self) -> None:
        """Shrink to a slim always-on-top title strip with restore control."""
        if self._collapsed:
            return
        self._expanded_size = self.size()
        self._collapsed = True
        for w in self._body_widgets:
            w.hide()
        self.btn_min.setText("□")
        self.btn_min.setToolTip("Expand overlay")
        self.title.setText("Interview Copilot — click □ to expand")
        self.setMinimumHeight(_COLLAPSED_HEIGHT)
        self.setMaximumHeight(_COLLAPSED_HEIGHT + 8)
        self.resize(max(self.width(), _MIN_WIDTH), _COLLAPSED_HEIGHT)
        self._on_status("Minimized to title bar")
        print("[UI] collapsed to title bar", flush=True)

    def _expand_from_titlebar(self) -> None:
        if not self._collapsed:
            return
        self._collapsed = False
        self.setMaximumHeight(16777215)
        self.setMinimumHeight(_MIN_HEIGHT)
        for w in self._body_widgets:
            w.show()
        self.btn_min.setText("−")
        self.btn_min.setToolTip("Minimize to title bar — click □ to expand again")
        self.title.setText("Interview Copilot")
        target = self._expanded_size
        self.resize(
            max(target.width(), _MIN_WIDTH),
            max(target.height(), _MIN_HEIGHT),
        )
        self._refresh_usage_labels()
        self._on_status("Expanded")
        print("[UI] expanded from title bar", flush=True)

    def _minimize_to_tray(self) -> None:
        self.hide()
        CTX.overlay_visible = False
        self._on_status("In system tray — click tray icon to restore")

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
        if self._collapsed:
            self._expand_from_titlebar()
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
