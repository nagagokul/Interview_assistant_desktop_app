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
    from src.services.ai_orchestrator import looks_like_interviewer_prompt

    a = "write a code in C++ and insert a node in Linux"
    b = "write a code in C++ and insert a node in Linux."
    assert text_similarity(a, b) >= 0.9
    assert looks_like_interviewer_prompt(a) is True
    assert looks_like_interviewer_prompt("ok") is False
    assert looks_like_interviewer_prompt("What is a mutex?") is True


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
