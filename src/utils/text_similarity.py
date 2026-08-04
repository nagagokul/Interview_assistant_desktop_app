"""Text helpers for diarization echo suppression (no Qt dependency)."""

from __future__ import annotations

from difflib import SequenceMatcher


def normalize_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def text_similarity(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()
