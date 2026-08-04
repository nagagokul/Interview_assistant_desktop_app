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
