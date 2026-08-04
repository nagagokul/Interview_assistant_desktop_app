"""Structured file + console logging under %APPDATA%/Copilot/logs/."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.core.paths import logs_dir


_CONFIGURED = False


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure root logger once; returns the application logger."""
    global _CONFIGURED
    logger = logging.getLogger("copilot")
    if _CONFIGURED:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_path: Path = logs_dir() / "copilot.log"
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))

    logger.addHandler(file_handler)
    logger.addHandler(console)

    # Quiet noisy third-party loggers
    for noisy in ("urllib3", "httpx", "httpcore", "chromadb", "openai", "groq"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    logger.info("Logging initialized -> %s", log_path)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    base = logging.getLogger("copilot")
    if name:
        return base.getChild(name)
    return base
