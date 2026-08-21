"""Unit tests for encryption, chunking, image diff, and database (no GUI)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest


def test_fernet_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # Reset path caches
    from src.core import paths

    paths.appdata_dir.cache_clear()
    paths.data_dir.cache_clear()
    paths.key_path.cache_clear() if hasattr(paths.key_path, "cache_clear") else None
    for fn in (paths.appdata_dir, paths.data_dir, paths.logs_dir, paths.chroma_dir, paths.documents_dir):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()

    from src.data.encryption import Encryptor, load_or_create_key

    load_or_create_key.cache_clear()
    enc = Encryptor()
    token = enc.encrypt("hello interview")
    assert enc.decrypt(token) == "hello interview"


def test_chunk_text_overlap() -> None:
    from src.services.rag_service import chunk_text

    text = ("Sentence one. " * 40) + ("Sentence two. " * 40)
    chunks = chunk_text(text, chunk_size=200, overlap=40)
    assert len(chunks) > 1
    assert all(len(c) <= 240 for c in chunks)


def test_local_vector_store_roundtrip(tmp_path: Path) -> None:
    from src.services.rag_service import LocalVectorStore

    store = LocalVectorStore(tmp_path / "rag.json", dim=64)
    store.add(
        ids=["a_0", "b_0"],
        documents=["python asyncio event loop interview", "gardening tomatoes and soil"],
        metadatas=[{"doc_id": "a", "filename": "resume.txt"}, {"doc_id": "b", "filename": "notes.txt"}],
    )
    docs, metas = store.query("asyncio interview python", top_k=1)
    assert docs
    assert "asyncio" in docs[0]
    assert metas[0]["filename"] == "resume.txt"
    # reload
    store2 = LocalVectorStore(tmp_path / "rag.json", dim=64)
    assert store2.count() == 2


def test_pixel_change_ratio() -> None:
    from src.utils.image_diff import pixel_change_ratio

    a = np.zeros((100, 100, 3), dtype=np.uint8)
    b = a.copy()
    assert pixel_change_ratio(a, b).changed is False
    b[0:50, 0:50] = 255
    assert pixel_change_ratio(a, b, threshold=0.01).changed is True


def test_pcm_wav_header() -> None:
    from src.utils.vad import pcm16_to_wav

    pcm = (b"\x00\x00" * 1600)  # 100ms @ 16kHz mono
    wav = pcm16_to_wav(pcm, 16000, 1)
    assert wav[:4] == b"RIFF"
    assert b"WAVE" in wav[:16]


def test_database_session_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.core import paths

    for fn in (paths.appdata_dir, paths.data_dir, paths.logs_dir, paths.chroma_dir, paths.documents_dir):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()

    from src.data.encryption import Encryptor, load_or_create_key

    load_or_create_key.cache_clear()
    from src.data.database import Database

    db = Database(path=tmp_path / "test.db", encryptor=Encryptor())
    session = db.create_session(title="Acme Interview", company="Acme", role="SWE")
    assert session.title == "Acme Interview"
    msg = db.add_message(session.id, role="assistant", content="Use a hash map.", source="ai")
    rows = db.list_messages(session.id)
    assert len(rows) == 1
    assert rows[0].content == "Use a hash map."
    assert rows[0].id == msg.id
    db.end_session(session.id)
    loaded = db.get_session(session.id)
    assert loaded is not None
    assert loaded.ended_at is not None


def test_event_bus_publish() -> None:
    from src.core.event_bus import EventBus, EventType

    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(EventType.STATUS, lambda e: seen.append(e.payload["message"]))
    bus.publish(EventType.STATUS, message="ok")
    assert seen == ["ok"]


def test_prompt_builder_includes_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.context import AppContext
    import src.services.ai_orchestrator as orch

    ctx = AppContext()
    ctx.add_transcript("interviewer", "What is the time complexity of binary search?")
    ctx.latest_ocr_text = "def binary_search(arr, x):"
    monkeypatch.setattr(orch, "CTX", ctx)
    ai = orch.AIOrchestrator()
    prompt, image = ai.build_prompt(user_hint="", include_image=False)
    assert "binary search" in prompt.lower()
    assert "binary_search" in prompt
    assert image is None


def test_markdown_to_html_code_fence() -> None:
    from src.utils.markdown_html import markdown_to_html

    html = markdown_to_html("## Answer\n\n```python\nprint(1)\n```\n\n**Done**")
    assert "<h3" in html
    assert "<pre" in html
    assert "print(1)" in html
    assert "<b>Done</b>" in html


def test_echo_similarity_and_interviewer_prompt() -> None:
    from src.utils.text_similarity import text_similarity
    from src.services.ai_orchestrator import (
        classify_intent,
        looks_like_chitchat,
        looks_like_interviewer_prompt,
        looks_like_technical_prompt,
        prompt_mode_for,
    )

    a = "write a code in C++ and insert a node in Linux"
    b = "write a code in C++ and insert a node in Linux."
    assert text_similarity(a, b) >= 0.9
    assert looks_like_interviewer_prompt(a) is True
    assert looks_like_interviewer_prompt("ok") is False
    assert looks_like_interviewer_prompt("What is a mutex?") is True
    assert looks_like_chitchat("Hello, are you there?") is True
    assert looks_like_chitchat("Hey, I'm audible, no?") is True
    assert looks_like_interviewer_prompt("Hello, are you there?") is False
    assert looks_like_technical_prompt(
        "So can you write the code in C++ to insert the node in linked list?"
    )
    assert looks_like_interviewer_prompt(
        "So can you write the code in C++ to insert the node in linked list?"
    )
    assert classify_intent("write the code in C++ for a linked list") == "coding"
    assert classify_intent("design microservices with a load balancer and sharding") == (
        "technical_discussion"
    )
    assert classify_intent("tell me about a time you showed leadership") == "behavioral"
    assert prompt_mode_for("technical_discussion") == "system_design"


def test_auto_ask_does_not_skip_when_streaming(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: coding Q arrived while Gemini still answering audio-check."""
    from src.services.ai_orchestrator import AIOrchestrator
    from src.core import context as ctx_mod

    orch = AIOrchestrator(hub=None)
    calls: list[dict] = []

    def _fake_ask(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(orch, "ask", _fake_ask)
    monkeypatch.setattr(ctx_mod.CTX, "is_ai_streaming", True)

    orch._schedule_auto_ask("write C++ code to insert a node in a linked list", mode="coding")
    assert orch._debounce_timer is not None
    orch._debounce_timer.cancel()
    # Fire immediately
    orch._debounce_timer.function()
    assert len(calls) == 1
    assert "linked list" in calls[0]["user_hint"]
    assert calls[0]["mode"] in ("auto", "coding")


def test_conversation_memory_last_n() -> None:
    from src.services.ai_orchestrator import ConversationMemory

    mem = ConversationMemory(maxlen=5)
    mem.append("[INTERVIEWER]", "Explain quicksort")
    mem.append("[CANDIDATE]", "Sure")
    mem.append("[INTERVIEWER]", "write a code in C++")
    block = mem.last_n(10)
    assert "[INTERVIEWER]" in block
    assert "[CANDIDATE]" in block
    assert "quicksort" in block
    assert "C++" in block


def test_split_default_device_input_output_pair() -> None:
    from src.utils.audio_devices import resolve_mic_device, resolve_loopback_device, split_default_device

    class _Pair:
        def __init__(self, inn, out):
            self.input = inn
            self.output = out

    assert split_default_device(_Pair(3, 7)) == (3, 7)
    assert split_default_device((1, 2)) == (1, 2)
    assert split_default_device(5) == (5, 5)
    # Without sounddevice, resolvers still return None safely
    assert resolve_mic_device(4) == 4
    assert resolve_loopback_device(9) == 9


def test_wasapi_extra_settings_only_for_wasapi_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: PaErrorCode -9984 when WasapiSettings hit an MME/DS mic."""
    from src.utils import audio_devices as ad

    class _FakeSD:
        class WasapiSettings:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        def __init__(self, host: str):
            self._host = host

        def query_devices(self, device=None):
            return {"hostapi": 0, "name": "Mic", "max_input_channels": 1}

        def query_hostapis(self, idx):
            return {"name": self._host}

        @property
        def default(self):
            class D:
                device = 0

            return D()

    assert ad.is_wasapi_device(0, _FakeSD("Windows WASAPI")) is True
    assert ad.is_wasapi_device(0, _FakeSD("MME")) is False
    assert ad.wasapi_extra_settings_for_device(0, _FakeSD("MME")) is None
    extra = ad.wasapi_extra_settings_for_device(0, _FakeSD("Windows WASAPI"))
    assert extra is not None
    assert getattr(extra, "kwargs", {}).get("exclusive") is False


def test_no_wasapi_settings_loopback_kwarg_in_source() -> None:
    """Regression: sounddevice 0.5.x crashes on WasapiSettings(loopback=True)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src"
    call_re = re.compile(r"WasapiSettings\s*\([^)]*\bloopback\s*=")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # Ignore comments / docstrings that mention the forbidden pattern
        code_lines = []
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            code_lines.append(line)
        code = "\n".join(code_lines)
        if call_re.search(code):
            offenders.append(str(path.relative_to(root.parent)))
    assert offenders == [], f"Invalid WasapiSettings(loopback=) in: {offenders}"


def test_resample_mono_identity_and_downsample() -> None:
    from src.utils.wasapi_loopback import resample_mono_f32

    x = np.linspace(-0.5, 0.5, 160, dtype=np.float32)
    assert resample_mono_f32(x, 16000, 16000).shape == (160,)
    y = resample_mono_f32(x, 48000, 16000)
    assert 50 <= len(y) <= 60


def test_open_wasapi_loopback_raises_without_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.utils.wasapi_loopback as wl

    monkeypatch.setattr(wl, "open_loopback_pyaudiowpatch", lambda target_rate=16000: None)
    monkeypatch.setattr(wl, "open_loopback_soundcard", lambda target_rate=16000: None)
    monkeypatch.setattr(wl, "open_loopback_stereo_mix", lambda target_rate=16000: None)
    with pytest.raises(RuntimeError, match="WASAPI loopback"):
        wl.open_wasapi_loopback()


def test_normalize_gemini_model_remaps_retired_ids() -> None:
    from src.core.config import normalize_gemini_model, AIConfig

    assert normalize_gemini_model("gemini-1.5-flash") == "gemini-flash-latest"
    assert normalize_gemini_model("models/gemini-1.5-flash") == "gemini-flash-latest"
    assert normalize_gemini_model("gemini-2.0-flash") == "gemini-flash-latest"
    assert normalize_gemini_model("gemini-3.5-flash") == "gemini-3.5-flash"
    assert AIConfig().gemini_model == "gemini-flash-latest"


def test_api_retry_helpers_parse_transient_and_retry_after() -> None:
    from src.utils.api_retry import (
        is_not_found_model_error,
        is_transient_api_error,
        parse_retry_after_seconds,
    )

    assert is_transient_api_error("503 UNAVAILABLE high demand") is True
    assert is_transient_api_error("Error code: 429 - Rate limit reached") is True
    assert is_transient_api_error("permission denied") is False
    assert is_not_found_model_error("404 NOT_FOUND model") is True
    assert parse_retry_after_seconds("Please try again in 3s.", default=1.0) == 3.0
    assert parse_retry_after_seconds("retry after 1500 ms", default=1.0) == 1.5


def test_api_usage_tracker_warns_near_free_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import api_usage as usage_mod
    from src.core.config import CONFIG

    monkeypatch.setattr(CONFIG.ai, "groq_rpm_limit", 10)
    monkeypatch.setattr(CONFIG.ai, "groq_daily_limit", 100)
    monkeypatch.setattr(CONFIG.ai, "usage_warn_ratio", 0.7)
    monkeypatch.setattr(CONFIG.ai, "usage_critical_ratio", 0.9)

    tracker = usage_mod.ApiUsageTracker()
    for _ in range(7):
        snap = tracker.record("groq")
    assert snap.rpm_used == 7
    assert snap.warn is True
    assert snap.critical is False

    for _ in range(3):
        snap = tracker.record("groq")
    assert snap.rpm_used == 10
    assert snap.critical is True
    assert tracker.would_exceed_rpm("groq") is True
    assert tracker.seconds_until_rpm_slot("groq") > 0


def test_gemini_stream_retries_transient_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """503 UNAVAILABLE should retry / fall back instead of failing immediately."""
    import sys
    import types as pytypes

    from src.services.ai_orchestrator import AIOrchestrator
    from src.core.config import CONFIG

    # Stub google.genai.types so the stream path can load without the SDK installed
    google_mod = pytypes.ModuleType("google")
    genai_mod = pytypes.ModuleType("google.genai")
    types_mod = pytypes.ModuleType("google.genai.types")

    class _Part:
        @staticmethod
        def from_text(text):
            return {"text": text}

        @staticmethod
        def from_bytes(data, mime_type):
            return {"data": data, "mime": mime_type}

    class _Content:
        def __init__(self, role, parts):
            self.role = role
            self.parts = parts

    class _GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    types_mod.Part = _Part
    types_mod.Content = _Content
    types_mod.GenerateContentConfig = _GenerateContentConfig
    genai_mod.types = types_mod
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)

    monkeypatch.setattr(CONFIG.ai, "gemini_max_retries", 2)
    monkeypatch.setattr(CONFIG.ai, "gemini_model", "gemini-flash-latest")
    monkeypatch.setattr("src.services.ai_orchestrator.time.sleep", lambda *_a, **_k: None)

    orch = AIOrchestrator(hub=None)
    calls = {"n": 0}

    class _FakeModels:
        def generate_content_stream(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError(
                    "503 UNAVAILABLE. {'error': {'message': 'high demand', 'status': 'UNAVAILABLE'}}"
                )

            class _Chunk:
                text = "ok-answer"

            yield _Chunk()

    class _FakeClient:
        models = _FakeModels()

    monkeypatch.setattr(orch, "_ensure_client", lambda: ("genai", _FakeClient()))
    monkeypatch.setattr(orch, "_wait_for_gemini_slot", lambda: None)
    monkeypatch.setattr(orch, "_emit_usage", lambda: None)

    out = "".join(orch._stream_tokens("prompt", None))
    assert out == "ok-answer"
    assert calls["n"] >= 2


def test_close_button_requests_quit_not_tray() -> None:
    """Regression: overlay ✕ must quit the app, not only hide to the tray."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    dashboard = (root / "src" / "ui" / "ui_dashboard.py").read_text(encoding="utf-8")
    main = (root / "src" / "main.py").read_text(encoding="utf-8")

    assert "quitRequested = pyqtSignal()" in dashboard
    assert "btn_close.clicked.connect(self._request_quit)" in dashboard
    assert "self.quitRequested.emit()" in dashboard
    assert "_minimize_to_tray" not in dashboard
    assert "dashboard.quitRequested.connect(_quit)" in main
    assert "tray.hide()" in main
