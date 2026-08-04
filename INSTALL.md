# Installation Guide — Interview Copilot (Windows)

Designed for **Windows 10/11**, Intel i5-class CPUs, **8 GB RAM**. No Electron, no browser runtime.

## 1. Prerequisites

1. **Python 3.11 or 3.12 (64-bit) — strongly recommended**  
   Download: https://www.python.org/downloads/release/python-31210/  
   During setup, enable **“Add python.exe to PATH”** and **“py launcher”**.  
   > **Avoid Python 3.14 for now.** Many packages (`chroma-hnswlib`, `webrtcvad`) have no
   > Windows wheels for 3.14, so pip tries to compile them and fails with
   > `Microsoft Visual C++ 14.0 is required`. This repo’s core `requirements.txt` no longer
   > needs those packages, but 3.11/3.12 is still the smoothest path.
2. **API keys**
   - Groq: https://console.groq.com → create key for `whisper-large-v3`
   - Google AI Studio: https://aistudio.google.com/apikey → key for `gemini-1.5-flash`
3. **Tesseract OCR** (for screen region text)
   - Installer: https://github.com/UB-Mannheim/tesseract/wiki  
   - Default path `C:\Program Files\Tesseract-OCR\` is auto-detected  
   - Or copy the install folder into `assets\tesseract\` for portable builds
4. **Microphone + speakers/headphones** with permission granted to the app  
   Loopback capture needs WASAPI (built into Windows).

## 2. Configure Secrets (`.env`)

```bat
copy .env.example .env
notepad .env
```

Set:

```
GROQ_API_KEY=gsk_...
GOOGLE_API_KEY=...
```

The app also loads `%APPDATA%\Copilot\.env` if present (useful after packaging).

## 3. Dev Run (before packaging)

Prefer creating a venv with **3.12** explicitly:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

If you only have Python 3.14 installed, the slim `requirements.txt` still works
(no Chroma / no webrtcvad compile). Optional extras:

```bat
pip install -r requirements-optional.txt
```

First launch creates:

```
%APPDATA%\Copilot\
  logs\copilot.log
  data\copilot.db          (Fernet-encrypted history)
  data\.master.key
  data\rag_index.json      (local vector index — no C++ needed)
  documents\               (uploaded resumes / JDs)
```

## 4. Build Portable EXE (double-click install)

From the repo root:

```bat
build.bat
```

Output:

```
dist\InterviewCopilot\InterviewCopilot.exe
```

Copy your `.env` next to the EXE (the build script does this if `.env` exists):

```
dist\InterviewCopilot\.env
```

Double-click `InterviewCopilot.exe`. A tray icon appears; the frameless overlay floats on top.

## 5. How to use (after install)

Full walkthrough with overlay map, hotkeys, interview workflow, modes, and checklist:

➡️ **[USAGE.md](USAGE.md)**

### First-session checklist

| Step | Action | Hotkey |
|------|--------|--------|
| 1 | Confirm Stealth is ON (button in overlay) | — |
| 2 | Drop your resume + job description onto the overlay | — |
| 3 | Click **OCR Region** and drag a box over the coding pad / shared screen | `Alt+S` |
| 4 | Click **OCR Watch** to continuously OCR changed pixels | — |
| 5 | Click **Listen** to start mic + system-audio diarization | — |
| 6 | Press **Ask** when you want a coached answer | `Alt+Enter` |
| 7 | Hide/show instantly if needed | `Alt+H` |
| 8 | Drag the opacity slider so the overlay stays subtle | — |

## 6. Resource Notes (8 GB RAM)

- No local LLM / Whisper weights — STT and vision go to Groq + Gemini.
- Chroma uses a tiny hash embedder if `sentence-transformers` is too heavy.
- PyInstaller build excludes `torch` / `tensorflow` / `matplotlib`.
- Prefer the **one-folder** build (default) over one-file for faster cold start.

## 7. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Failed building wheel for chroma-hnswlib / webrtcvad` | You hit packages that need MSVC on Python 3.14. **Pull latest code** and use slim `requirements.txt` (Chroma/VAD are optional). Or install **Python 3.12** and recreate the venv. |
| `Microsoft Visual C++ 14.0 or greater is required` | Do **not** install Build Tools just for this app. Switch to Python 3.11/3.12, or stay on the slim requirements (energy VAD + local RAG). |
| “Missing API keys” dialog | Place `.env` beside EXE or in `%APPDATA%\Copilot\.env` |
| No interviewer transcripts | Ensure call audio plays through speakers/headphones; grant mic privacy; try Stereo Mix / WASAPI |
| `WasapiSettings ... unexpected keyword argument 'loopback'` | Fixed in current tree — pull latest. Then `pip install PyAudioWPatch soundcard` (Windows). sounddevice 0.5.x cannot take `loopback=True`. |
| OCR empty | Install Tesseract; verify `tesseract --version` in cmd |
| Overlay appears in screen share | Toggle **Stealth** off/on; requires Windows 10 2004+ |
| **Overlay vanishes after Stealth ON** | Bug in older builds (Win32 layered alpha + capture exclusion). **Pull latest**, restart. Recovery now: tray icon → **Restore Overlay (if invisible)** or **Toggle Stealth**. Stealth keeps the window visible to you while hiding it from Zoom/Teams/Meet. |
| Hotkeys do nothing | Run EXE as the logged-in user (not a different elevated account); `keyboard` needs input access |
| High CPU | Stop OCR Watch when idle; reduce opacity animations; close unused browser tabs |

### Fix the exact pip error you hit (Python 3.14)

```bat
cd D:\Interview_assistant_desktop_app
git pull
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

Better long-term (recommended):

```bat
REM Install Python 3.12 from python.org, then:
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 8. Uninstall

Delete:

- The `dist\InterviewCopilot` folder (or wherever you placed the portable build)
- `%APPDATA%\Copilot` (history, keys, embeddings)

No installer registry entries are required for the portable build.
