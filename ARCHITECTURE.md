# Interview Copilot — Architecture & Windows Workflow

Native Windows desktop AI interview assistant built with **PyQt6** (no Electron, no local
WebSocket ports). Heavy vision / STT / LLM work is offloaded to cloud APIs so an Intel i5
with 8 GB RAM stays fluid.

## ASCII Workflow Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                        WINDOWS DESKTOP (Candidate)                       │
 │                                                                          │
 │  ┌─────────────────────┐   ┌──────────────────────┐   ┌───────────────┐  │
 │  │ Speakers/Headphones │   │   Microphone (Mic)   │   │ Screen Region │  │
 │  │ (Interviewer’s VO)  │   │   (Candidate VO)     │   │  (code / JD)  │  │
 │  └──────────┬──────────┘   └──────────┬───────────┘   └───────┬───────┘  │
 │             │ WASAPI                  │ PyAudio /             │ DXCAM /  │
 │             │ Loopback                │ sounddevice           │ mss grab │
 │             ▼                         ▼                       ▼          │
 │  ┌──────────────────────────────────────────┐   ┌─────────────────────┐  │
 │  │     AudioCaptureService  (QThread)       │   │  OCRRegionService   │  │
 │  │  VAD → PCM ring → utterance segments     │   │  pixel-diff <100ms  │  │
 │  └──────────────────┬───────────────────────┘   │  Tesseract OCR      │  │
 │                     │                           └──────────┬──────────┘  │
 │                     ▼                                      │             │
 │         asyncio / ThreadPool queues                        │             │
 │         (native memory slots — EventBus)                   │             │
 │                     │                                      │             │
 └─────────────────────┼──────────────────────────────────────┼─────────────┘
                       │                                      │
                       ▼                                      ▼
          ┌────────────────────────┐            ┌─────────────────────────┐
          │  Groq Cloud API        │            │  Local Tesseract + JPEG │
          │  whisper-large-v3      │            │  (only on pixel change) │
          │  target < 300 ms       │            └────────────┬────────────┘
          └───────────┬────────────┘                         │
                      │ transcript                           │ OCR text +
                      │ [interviewer|candidate]              │ region image
                      ▼                                      ▼
              ┌──────────────────────────────────────────────────┐
              │              AppContext (shared memory)           │
              │  transcripts | ocr_text | resume | rag_context    │
              └───────────────────────┬──────────────────────────┘
                                      │
                                      ▼
              ┌──────────────────────────────────────────────────┐
              │           AIOrchestrator (streaming)              │
              │  system prompt + RAG + transcript + OCR image     │
              │  → Google AI Studio  gemini-flash-latest           │
              │  target < 400 ms first token / < 2 s full answer  │
              └───────────────────────┬──────────────────────────┘
                                      │ token stream (<30 ms UI)
                                      ▼
              ┌──────────────────────────────────────────────────┐
              │   PyQt6 Frameless Overlay  (ui_dashboard.py)      │
              │   Split view: Interviewer | Candidate | Assistant │
              │   SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)│
              │   → visible locally, invisible to Zoom/Teams/Meet │
              └──────────────────────────────────────────────────┘

 Side channels:
   Drag-drop docs → RAGManager → ChromaDB (embedded) → resume/JD context
   Hotkeys (Alt+H / Alt+S / Alt+Enter) → KeyHookService → EventBus
   History → Fernet-encrypted SQLite  (%APPDATA%/Copilot/data/copilot.db)
   Logs    → %APPDATA%/Copilot/logs/copilot.log
```

## Module Map (SOLID)

| Module | Responsibility | Threading |
|--------|----------------|-----------|
| `AudioCaptureService` | WASAPI loopback + mic, VAD, Groq STT | daemon threads + ThreadPool |
| `OCRRegionService` | region grab, differential OCR | daemon thread |
| `AIOrchestrator` | prompt stitch + Gemini stream | worker thread |
| `RAGManager` | chunk, embed, Chroma query | lock-guarded |
| `KeyHookService` | global hotkeys | OS hook callbacks |
| `StealthService` | Win32 display affinity / opacity | UI thread |
| `OverlayDashboard` | frameless UI, DND, panels | Qt main thread |
| `EventBus` / `AppContext` | in-process pub/sub + state | RLock |

## Latency Budget

| Path | Target |
|------|--------|
| System audio → Groq Whisper | < 300 ms |
| DXCAM/mss region + diff check | < 100 ms |
| Gemini 1.5 Flash first useful tokens | < 400 ms |
| Stream token → QText/bubble paint | < 30 ms |
| Full context-aware answer | < 2 s |

## Stealth Mode

Uses the documented Win32 API:

```c
SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE); // 0x11
```

The overlay remains visible on the candidate’s desktop compositor but is excluded from
Desktop Duplication / BitBlt capture used by Zoom, Teams, Meet, and Discord.
