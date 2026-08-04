"""Application path resolution for portable and installed layouts."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path


APP_NAME = "InterviewCopilot"
APP_DIR_NAME = "Copilot"


def is_frozen() -> bool:
    """True when running from a PyInstaller / Briefcase bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


@lru_cache(maxsize=1)
def project_root() -> Path:
    """Repository / package root (dev) or extraction root (frozen)."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def appdata_dir() -> Path:
    """
    Persistent user data directory.

    Windows: %APPDATA%/Copilot
    Fallback (dev/non-Windows): ~/.copilot
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            path = Path(base) / APP_DIR_NAME
        else:
            path = Path.home() / "AppData" / "Roaming" / APP_DIR_NAME
    else:
        path = Path.home() / ".copilot"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def logs_dir() -> Path:
    path = appdata_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def data_dir() -> Path:
    path = appdata_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def chroma_dir() -> Path:
    path = appdata_dir() / "chroma"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def documents_dir() -> Path:
    path = appdata_dir() / "documents"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def cache_dir() -> Path:
    path = appdata_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "copilot.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def key_path() -> Path:
    """Path to the local Fernet encryption key file."""
    return data_dir() / ".master.key"


def assets_dir() -> Path:
    return project_root() / "assets"


def prompts_dir() -> Path:
    return assets_dir() / "prompts"


def icons_dir() -> Path:
    return assets_dir() / "icons"


def tesseract_dir() -> Path:
    """Bundled Tesseract binaries (packaged next to the EXE or under assets)."""
    candidates = [
        project_root() / "tesseract",
        project_root() / "assets" / "tesseract",
        appdata_dir() / "tesseract",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def tesseract_executable() -> Path | None:
    """Resolve tesseract.exe / tesseract binary if present."""
    base = tesseract_dir()
    names = ("tesseract.exe", "tesseract")
    for name in names:
        candidate = base / name
        if candidate.exists():
            return candidate
    # Common Windows install locations
    for extra in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ):
        if extra.exists():
            return extra
    return None


def env_file_candidates() -> list[Path]:
    """Ordered list of .env locations to try loading."""
    return [
        Path.cwd() / ".env",
        appdata_dir() / ".env",
        project_root() / ".env",
        Path(sys.executable).resolve().parent / ".env" if is_frozen() else project_root() / ".env",
    ]
