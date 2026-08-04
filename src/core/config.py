"""Central configuration loaded from environment and local settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.core.paths import env_file_candidates, settings_path


def _load_dotenv() -> None:
    for candidate in env_file_candidates():
        if candidate.is_file():
            load_dotenv(candidate, override=False)


_load_dotenv()


@dataclass
class AudioConfig:
    sample_rate: int = 16_000
    channels: int = 1
    sample_width: int = 2  # 16-bit PCM
    chunk_ms: int = 30
    vad_aggressiveness: int = 2  # 0-3
    silence_hangover_ms: int = 600
    max_utterance_ms: int = 12_000
    min_utterance_ms: int = 400
    mic_device_index: int | None = None
    loopback_device_index: int | None = None
    noise_suppress: bool = True


@dataclass
class OCRConfig:
    poll_interval_ms: int = 80
    change_threshold: float = 0.018  # fraction of pixels that must change
    tesseract_lang: str = "eng"
    tesseract_psm: int = 6
    max_region_width: int = 1920
    max_region_height: int = 1080
    jpeg_quality: int = 85


@dataclass
class AIConfig:
    gemini_model: str = "gemini-1.5-flash"
    groq_model: str = "whisper-large-v3"
    temperature: float = 0.35
    max_output_tokens: int = 2048
    stream: bool = True
    system_prompt_file: str = "system_interview.txt"


@dataclass
class RAGConfig:
    collection_name: str = "interview_docs"
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 4
    embed_model: str = "all-MiniLM-L6-v2"


@dataclass
class UIConfig:
    opacity: float = 0.92
    width: int = 420
    height: int = 640
    always_on_top: bool = True
    stealth_enabled: bool = True
    hotkey_toggle: str = "alt+h"
    hotkey_snip: str = "alt+s"
    hotkey_ask: str = "alt+enter"
    theme: str = "dark"


@dataclass
class AppConfig:
    groq_api_key: str = ""
    google_api_key: str = ""
    audio: AudioConfig = field(default_factory=AudioConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    log_level: str = "INFO"

    def validate_keys(self) -> list[str]:
        missing: list[str] = []
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if not self.google_api_key:
            missing.append("GOOGLE_API_KEY")
        return missing


def _nested_update(target: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _nested_update(target[key], value)
        else:
            target[key] = value
    return target


def _from_dict(data: dict[str, Any]) -> AppConfig:
    audio = AudioConfig(**{k: v for k, v in data.get("audio", {}).items() if k in AudioConfig.__dataclass_fields__})
    ocr = OCRConfig(**{k: v for k, v in data.get("ocr", {}).items() if k in OCRConfig.__dataclass_fields__})
    ai = AIConfig(**{k: v for k, v in data.get("ai", {}).items() if k in AIConfig.__dataclass_fields__})
    rag = RAGConfig(**{k: v for k, v in data.get("rag", {}).items() if k in RAGConfig.__dataclass_fields__})
    ui = UIConfig(**{k: v for k, v in data.get("ui", {}).items() if k in UIConfig.__dataclass_fields__})
    return AppConfig(
        groq_api_key=data.get("groq_api_key", ""),
        google_api_key=data.get("google_api_key", ""),
        audio=audio,
        ocr=ocr,
        ai=ai,
        rag=rag,
        ui=ui,
        log_level=data.get("log_level", "INFO"),
    )


def load_config() -> AppConfig:
    """Merge defaults <- settings.json <- environment variables."""
    cfg = AppConfig()
    path = settings_path()
    if path.is_file():
        try:
            disk = json.loads(path.read_text(encoding="utf-8"))
            merged = asdict(cfg)
            _nested_update(merged, disk)
            cfg = _from_dict(merged)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    cfg.groq_api_key = os.environ.get("GROQ_API_KEY", cfg.groq_api_key)
    cfg.google_api_key = (
        os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or cfg.google_api_key
    )
    cfg.log_level = os.environ.get("LOG_LEVEL", cfg.log_level)

    if model := os.environ.get("GEMINI_MODEL"):
        cfg.ai.gemini_model = model
    if model := os.environ.get("GROQ_WHISPER_MODEL"):
        cfg.ai.groq_model = model

    return cfg


def save_config(cfg: AppConfig) -> None:
    """Persist non-secret UI/runtime settings (API keys stay in .env)."""
    payload = asdict(cfg)
    # Never write secrets into settings.json
    payload.pop("groq_api_key", None)
    payload.pop("google_api_key", None)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# Process-wide singleton
CONFIG: AppConfig = load_config()
