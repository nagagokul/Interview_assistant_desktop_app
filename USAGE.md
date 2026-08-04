# How to Use Interview Copilot

Step-by-step guide for running the app during a real interview (coding, system design, or behavioral).

---

## Before you start (one-time setup)

1. Install dependencies and set API keys — see **[INSTALL.md](INSTALL.md)**.
2. Make sure `.env` has:
   ```
   GROQ_API_KEY=...
   GOOGLE_API_KEY=...
   ```
3. Install **Tesseract OCR** if you want screen-region text reading.
4. Launch:
   ```bat
   .venv\Scripts\activate
   python main.py
   ```
   Or double-click `dist\InterviewCopilot\InterviewCopilot.exe` after building.

On launch you should see:
- A **frameless dark overlay** floating on top of other windows
- A **system tray icon** (Interview Copilot)

---

## Overlay layout (what each part does)

```
┌─────────────────────────────────────────────┐
│ Interview Copilot          status    Hide ✕ │
├─────────────────────────────────────────────┤
│ [Listen] [OCR Region] [OCR Watch] …         │
├─────────────────────────────────────────────┤
│ Opacity ────────●────────                   │
├─────────────────────────────────────────────┤
│ Live Conversation Stream   (TOP)            │
│  ┌ blue Interviewer bubble (left) ─────┐    │
│  │              grey You bubble (right) ┘    │
│                                             │
│ ─── splitter ─────────────────────────────  │
│ AI Copilot Core Guidance   (BOTTOM)         │
│  streaming Markdown / code / complexity     │
├─────────────────────────────────────────────┤
│ OCR peek                                    │
├─────────────────────────────────────────────┤
│ [mode ▼]  [hint or question…]      [Ask]    │
└─────────────────────────────────────────────┘
```

| Control | What it does |
|---------|----------------|
| **Listen / Stop** | Starts or stops dual audio capture (your mic + system/loopback audio from the call) |
| **OCR Region** | Opens a full-screen snip tool — drag a box over the problem / IDE / shared screen |
| **OCR Watch** | Continuously re-reads that region when pixels change |
| **Stealth** | Toggles screen-share invisibility (`WDA_EXCLUDEFROMCAPTURE`) |
| **Docs** | File picker to upload resume / JD / notes for RAG |
| **Clear** | Clears conversation + AI panels |
| **Opacity** | Makes the overlay more or less transparent (disabled while Stealth is ON) |
| **Mode** | `auto` · `coding` · `system_design` · `behavioral` · `debug` |
| **Ask** | Sends live transcript + OCR + resume context to Gemini (streams into bottom panel) |

Terminal verification logs (watch these if text does not appear):

```
[AUDIO CAPTURED] → [GROQ TRANSCRIPT RECEIVED] → [UI TEXT APPENDED]
[GEMINI STREAM START] → [UI TEXT APPENDED] ai_chunk …
```

---

## Hotkeys

| Hotkey | Action |
|--------|--------|
| `Alt+H` | Hide / show the overlay instantly |
| `Alt+S` | Start OCR region selection (snip) |
| `Alt+Enter` | Ask AI using current context |
| `Esc` | Cancel snipping (while the snip overlay is open) |

Drag the overlay by clicking the title area. Use the tray icon → **Quit** to exit cleanly.

---

## Recommended workflow (live interview)

### 1. Prepare (2 minutes before the call)

1. Open Interview Copilot.
2. Confirm **Stealth** is ON (so Zoom/Teams/Meet/Discord screen share does not show the overlay).
3. Drag your **resume** (and job description if you have one) onto the overlay, or click **Docs**.
4. Lower **Opacity** to something comfortable (e.g. 70–85%).
5. Place the overlay on a second monitor if you have one, or in a corner that won’t cover the coding pad.

### 2. Select the screen region to watch

1. Press `Alt+S` (or click **OCR Region**).
2. Drag a rectangle over:
   - the coding problem statement, or
   - the IDE editor, or
   - a system-design whiteboard / shared doc
3. Click **OCR Watch** so the app keeps reading when the content changes.
4. Open the **OCR** tab to verify text is being captured.

### 3. Start listening

1. Join the interview call with audio playing through **speakers or headphones** (needed for interviewer loopback).
2. Click **Listen**.
3. Open the **Live** tab:
   - Left = **Interviewer** (system audio)
   - Right = **You** (microphone)
4. Speak a test sentence and confirm your side updates. Ask the interviewer to speak (or play a short test) and confirm their side updates.

### 4. Get AI help during the interview

**Automatic:** when the interviewer asks a question ending with `?`, the app can trigger an answer automatically.

**Manual (recommended for control):**
1. Choose a **mode** (`coding`, `system_design`, `behavioral`, `debug`, or `auto`).
2. Optionally type a short hint, e.g. `optimize for O(n)`, `STAR for conflict example`, `explain trade-offs`.
3. Press **Ask** or `Alt+Enter`.
4. Switch to the **Assistant** tab and read the streaming answer.

The AI uses:
- live transcript (interviewer vs you)
- OCR text / region image
- resume + job description + uploaded notes

### 5. Hide quickly if needed

- Press `Alt+H` to hide the overlay.
- Press `Alt+H` again to bring it back.
- Tray icon → **Show / Hide Overlay** does the same.

### 6. End the session

1. Click **Stop** (Listen) and **OCR Stop** if running.
2. Tray icon → **Quit**.
3. History is saved encrypted under `%APPDATA%\Copilot\`.

---

## Mode guide (when to use each)

| Mode | Use when |
|------|----------|
| `auto` | Default — let the model infer from transcript + OCR |
| `coding` | LeetCode-style problems, algorithms, complexity, code |
| `system_design` | Architecture, scalability, trade-offs, component diagrams |
| `behavioral` | STAR stories grounded in your uploaded resume |
| `debug` | Broken code / stack traces visible in the OCR region |

---

## Uploading documents (RAG)

Supported: `.pdf`, `.txt`, `.md`, `.docx`, `.json`

Ways to upload:
1. **Drag and drop** files onto the overlay
2. Click **Docs** and multi-select files

Tips:
- Name resume files with `resume` or `cv` in the filename
- Name job posts with `jd` / `job` / `description` in the filename  
  (helps the app classify them automatically)

---

## Stealth / screen-share notes

- **Stealth ON** = you can see the overlay; screen-sharing apps typically capture empty space there.
- Requires Windows 10 version 2004 or newer.
- If the overlay still appears in a share preview, toggle **Stealth** off → on, or restart the app after joining the call.
- Always do a quick **share preview check** before the interview starts.

---

## Typical session checklist

- [ ] `.env` keys set
- [ ] App launched; tray icon visible
- [ ] Stealth ON; opacity set
- [ ] Resume (+ JD) uploaded
- [ ] OCR region drawn over problem / IDE
- [ ] OCR Watch running; OCR tab shows text
- [ ] Listen started; Live tab shows both speakers
- [ ] Mode selected; Ask / `Alt+Enter` works
- [ ] `Alt+H` hide/show verified
- [ ] Screen-share preview checked

---

## Quick troubleshooting while using

| Problem | What to try |
|---------|-------------|
| No interviewer transcript | Play call audio on speakers/headphones (not exclusive-mode only); click Listen again |
| No mic transcript | Allow mic access in Windows Privacy settings |
| OCR blank | Install Tesseract; redraw a tighter region; ensure OCR Watch is on |
| AI error / no answer | Check `GOOGLE_API_KEY` in `.env`; check status bar message |
| Transcription error | Check `GROQ_API_KEY`; check internet |
| Overlay shows in screen share | Toggle Stealth; Windows 10 2004+ required |
| Hotkeys ignored | Don’t run as a different elevated user; refocus desktop and retry |

Logs: `%APPDATA%\Copilot\logs\copilot.log`

---

## Responsible use

Use this tool in line with your interviewer’s and employer’s policies. You are responsible for how you use the assistant during interviews.

For install/build details see **[INSTALL.md](INSTALL.md)**. For internals see **[ARCHITECTURE.md](ARCHITECTURE.md)**.
