# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Interview Copilot portable folder build."""

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "assets" / "prompts"), "assets/prompts"),
    (str(ROOT / "assets" / "icons"), "assets/icons"),
    (str(ROOT / ".env.example"), "."),
]

# Bundle Tesseract if the operator placed binaries under assets/tesseract
tess = ROOT / "assets" / "tesseract"
if tess.exists():
    datas.append((str(tess), "tesseract"))

hiddenimports = [
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "numpy",
    "PIL",
    "mss",
    "sounddevice",
    "groq",
    "google.genai",
    "google.generativeai",
    "chromadb",
    "chromadb.api",
    "chromadb.config",
    "pypdf",
    "docx",
    "cryptography",
    "dotenv",
    "keyboard",
    "pytesseract",
    "webrtcvad",
    "src.main",
    "src.core.config",
    "src.core.context",
    "src.core.event_bus",
    "src.core.paths",
    "src.core.logging_setup",
    "src.services.audio_service",
    "src.services.ocr_service",
    "src.services.ai_orchestrator",
    "src.services.rag_service",
    "src.services.key_hook_service",
    "src.services.stealth_service",
    "src.ui.ui_dashboard",
    "src.ui.snipping_widget",
    "src.ui.chat_panel",
    "src.ui.system_tray",
    "src.ui.styles",
    "src.data.database",
    "src.data.encryption",
    "src.data.models",
    "src.utils.vad",
    "src.utils.image_diff",
    "src.utils.win32_helpers",
]

if sys.platform == "win32":
    hiddenimports.append("dxcam")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "torch",
        "tensorflow",
        "IPython",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# One-folder build is more reliable for Tesseract + chroma natives on 8GB machines
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InterviewCopilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI app — no console flash
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icons" / "app.ico")
    if (ROOT / "assets" / "icons" / "app.ico").exists()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="InterviewCopilot",
)
