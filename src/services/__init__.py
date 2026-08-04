"""Service package exports (lazy-safe — avoid importing PyQt at package import)."""

from __future__ import annotations

__all__ = [
    "AIOrchestrator",
    "AudioCaptureService",
    "KeyHookService",
    "OCRRegionService",
    "RAGManager",
    "StealthService",
]


def __getattr__(name: str):
    if name == "AIOrchestrator":
        from src.services.ai_orchestrator import AIOrchestrator

        return AIOrchestrator
    if name == "AudioCaptureService":
        from src.services.audio_service import AudioCaptureService

        return AudioCaptureService
    if name == "KeyHookService":
        from src.services.key_hook_service import KeyHookService

        return KeyHookService
    if name == "OCRRegionService":
        from src.services.ocr_service import OCRRegionService

        return OCRRegionService
    if name == "RAGManager":
        from src.services.rag_service import RAGManager

        return RAGManager
    if name == "StealthService":
        from src.services.stealth_service import StealthService

        return StealthService
    raise AttributeError(name)
