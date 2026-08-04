# Interview Copilot

Native **Windows** AI Interview Copilot (Parakeet-style) built with **PyQt6** — not Electron.

Near-instant coaching for coding interviews, system design, debugging, and STAR behavioral answers on resource-constrained hardware (**Intel i5 / 8 GB RAM**).

## Highlights

- **Dual-stream diarization** — WASAPI loopback (interviewer) + microphone (you) → Groq `whisper-large-v3`
- **Dynamic region OCR** — snip any screen box; differential pixel checks; Tesseract locally
- **Gemini 1.5 Flash** streaming answers with resume/JD RAG (ChromaDB)
- **Stealth overlay** — `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` hides the UI from Zoom / Teams / Meet / Discord capture
- **Encrypted SQLite** history under `%APPDATA%\Copilot\`
- **Portable `.exe`** via PyInstaller (`build.bat`)

## Quick Start

Use **Python 3.11 or 3.12** (avoid 3.14 — missing native wheels for optional deps).

```bat
copy .env.example .env
REM edit .env with GROQ_API_KEY and GOOGLE_API_KEY

py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Core install needs **no Microsoft C++ Build Tools**. ChromaDB / webrtcvad are optional
(`requirements-optional.txt`); the app falls back to energy-based VAD and a local JSON RAG index.

Build a portable app:

```bat
build.bat
dist\InterviewCopilot\InterviewCopilot.exe
```

Full setup: **[INSTALL.md](INSTALL.md)** · **How to use: [USAGE.md](USAGE.md)** · Architecture: **[ARCHITECTURE.md](ARCHITECTURE.md)** · Storage: **[SCHEMA.md](SCHEMA.md)**

## How to use (short version)

1. Launch with `python main.py` (after install).
2. Turn **Stealth** ON and drop your **resume** onto the overlay.
3. Press `Alt+S`, drag a box over the coding pad / problem, then click **OCR Watch**.
4. Click **Listen** so interviewer (speakers) and you (mic) are transcribed.
5. Press `Alt+Enter` (or **Ask**) for a context-aware answer in the **Assistant** tab.
6. Press `Alt+H` anytime to hide/show the overlay.

Step-by-step walkthrough, modes, and checklist: **[USAGE.md](USAGE.md)**

## Production Folder Structure

```
Interview_assistant_desktop_app/
├── main.py / ui_dashboard.py / audio_service.py / ocr_service.py
├── ai_orchestrator.py / rag_service.py          # top-level re-exports
├── src/
│   ├── main.py                                  # bootstrap, tray, hotkeys
│   ├── core/                                    # config, context, EventBus, paths
│   ├── services/                                # audio, OCR, AI, RAG, stealth, keys
│   ├── ui/                                      # overlay, snip, chat, tray, styles
│   ├── data/                                    # encrypted SQLite + models
│   └── utils/                                   # VAD, image diff, Win32 helpers
├── assets/prompts/  assets/icons/  assets/tesseract/
├── packaging/InterviewCopilot.spec
├── scripts/download_tesseract.ps1
├── build.bat
├── requirements.txt
└── tests/
```

## Hotkeys

| Shortcut | Action |
|----------|--------|
| `Alt+H` | Hide / show overlay |
| `Alt+S` | Draw OCR region |
| `Alt+Enter` | Ask AI with live context |

## Tech Stack

| Layer | Choice |
|-------|--------|
| UI | PyQt6 frameless dark overlay |
| STT | Groq Whisper large-v3 |
| LLM / Vision | Google `gemini-1.5-flash` (`google-genai`) |
| Capture | sounddevice WASAPI loopback + mss/dxcam |
| OCR | Tesseract (bundled or system) |
| RAG | ChromaDB embedded |
| Package | PyInstaller one-folder EXE |

## License / Ethics

Use responsibly and in accordance with your interviewer’s and employer’s policies.
This repository is a technical implementation of a desktop assistant; you are responsible for how you use it.
