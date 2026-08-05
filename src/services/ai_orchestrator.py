"""
AIOrchestrator — rolling conversation memory + Gemini Flash streaming.

- Thread-safe chronological transcript state tagged [INTERVIEWER]/[CANDIDATE]
- Auto-triggers on interviewer turns (?, pause ≥1.5s after substantive ask)
- Local intent classifier auto-switches coding / technical_discussion / behavioral
- Streams tokens via StreamHub / pyqtSignals only (never touches widgets)
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Deque, Generator

try:
    from PyQt6.QtCore import QObject, pyqtSignal
except ImportError:  # headless / unit-test environments without PyQt6 wheels
    class QObject:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _DummySignal:
        def emit(self, *args: Any, **kwargs: Any) -> None:
            return None

        def connect(self, *args: Any, **kwargs: Any) -> None:
            return None

    def pyqtSignal(*_a: Any, **_k: Any) -> _DummySignal:  # type: ignore[misc]
        return _DummySignal()

from src.core.config import CONFIG, GEMINI_MODEL_FALLBACKS, normalize_gemini_model
from src.core.context import CTX
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger
from src.core.paths import prompts_dir
from src.data.database import get_db

if TYPE_CHECKING:
    from src.core.stream_hub import StreamHub

log = get_logger("ai")

DEFAULT_SYSTEM_PROMPT = """SYSTEM_INSTRUCTION: You are an elite silent technical copilot. Ignore casual banter if a technical question has been asked. If the transcript contains a programming request, immediately stop conversational chatting and output the optimized code, complexity analysis, and implementation hints in clean Markdown.

You are Interview Copilot — an elite, discreet technical interview assistant.

Mission: Deliver nearly instant, high-signal help during coding interviews, system design,
debugging, and behavioral (STAR) conversations.

Rules:
1. Be concise. Prefer short, interview-ready answers over essays.
2. Match the language the interviewer is using (C, C++, Python, Java, SQL, Bash, etc.).
3. For coding: provide correct, optimized solutions with time/space complexity in Markdown fences.
4. For system design / technical discussion: give clear components, trade-offs, and a crisp diagram in ASCII/Markdown.
5. For behavioral: use STAR grounded in the candidate's resume only.
6. Generate likely follow-up questions the interviewer may ask next.
7. Never invent resume facts — use only provided resume/RAG context.
8. If screen OCR shows a problem statement or code, prioritize that as the question.
9. Output structure when helpful:
   - Answer
   - Complexity (if coding)
   - Follow-ups
10. The latest substantive [INTERVIEWER] turn is the primary request to answer NOW.
    Never answer mic checks / "are you there?" once a real technical ask exists in the transcript.
"""

UI_MODES = ("auto", "coding", "technical_discussion", "behavioral", "debug")

_CODING_KEYWORDS = (
    r"write\s+code",
    r"write\s+the\s+code",
    r"implement",
    r"\bc\+\+\b",
    r"\bpython\b",
    r"\bjava\b",
    r"\bleetcode\b",
    r"linked\s*list",
    r"\barray\b",
    r"\bfunction\b",
    r"\balgorithm\b",
    r"\bcode\b",
    r"insert\s+(a\s+)?node",
    r"binary\s+tree",
    r"\bpointer\b",
    r"\bstack\b",
    r"\bqueue\b",
    r"\bgraph\b",
    r"time\s+complexity",
    r"\bsort\b",
    r"\bdebug\b",
    r"\boptimize\b",
)

_TECH_DISCUSSION_KEYWORDS = (
    r"system\s+design",
    r"database\s+scalability",
    r"\bmicroservices?\b",
    r"\blatency\b",
    r"load\s+balancer",
    r"\bcache\b",
    r"\bcaching\b",
    r"\bsharding\b",
    r"\bscalability\b",
    r"\bthroughput\b",
    r"\bcdn\b",
    r"\breplication\b",
    r"\bconsistency\b",
    r"\barchitecture\b",
    r"design\s+a\s+(system|service|api)",
)

_BEHAVIORAL_KEYWORDS = (
    r"tell\s+me\s+about\s+a\s+time",
    r"\bconflict\b",
    r"\bweakness(es)?\b",
    r"\bleadership\b",
    r"project\s+experience",
    r"\bstar\s+method\b",
    r"tell\s+me\s+about\s+yourself",
    r"greatest\s+strength",
    r"work\s+with\s+(a\s+)?team",
    r"difficult\s+(coworker|situation|stakeholder)",
)

_CODING_RE = re.compile("|".join(_CODING_KEYWORDS), re.IGNORECASE)
_TECH_RE = re.compile("|".join(_TECH_DISCUSSION_KEYWORDS), re.IGNORECASE)
_BEHAVIORAL_RE = re.compile("|".join(_BEHAVIORAL_KEYWORDS), re.IGNORECASE)


def _load_system_prompt() -> str:
    path = prompts_dir() / CONFIG.ai.system_prompt_file
    if path.is_file():
        body = path.read_text(encoding="utf-8")
        if "SYSTEM_INSTRUCTION:" not in body:
            return (
                "SYSTEM_INSTRUCTION: You are an elite silent technical copilot. "
                "Ignore casual banter if a technical question has been asked. "
                "If the transcript contains a programming request, immediately stop "
                "conversational chatting and output the optimized code, complexity "
                "analysis, and implementation hints in clean Markdown.\n\n"
                + body
            )
        return body
    try:
        prompts_dir().mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_SYSTEM_PROMPT, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_SYSTEM_PROMPT


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def classify_intent(text: str) -> str:
    """Fast local intent → coding | technical_discussion | behavioral | auto."""
    blob = (text or "").strip()
    if not blob:
        return "auto"
    if _CODING_RE.search(blob):
        return "coding"
    if _TECH_RE.search(blob):
        return "technical_discussion"
    if _BEHAVIORAL_RE.search(blob):
        return "behavioral"
    return "auto"


def prompt_mode_for(ui_mode: str) -> str:
    """Map UI combo values onto prompt instruction keys."""
    m = (ui_mode or "auto").strip().lower()
    if m in ("technical_discussion", "system_design"):
        return "system_design"
    if m in ("coding", "behavioral", "debug", "auto"):
        return m
    return "auto"


class ConversationMemory:
    """Thread-safe rolling transcript + interviewer sliding window."""

    def __init__(self, maxlen: int = 40) -> None:
        self._lock = threading.RLock()
        self._turns: Deque[str] = deque(maxlen=maxlen)
        self._interviewer: Deque[str] = deque(maxlen=12)

    def append(self, tag: str, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        line = f"{_stamp()} - {tag}: {text}"
        with self._lock:
            self._turns.append(line)
            if tag == "[INTERVIEWER]":
                self._interviewer.append(text)
        print(f"[CONTEXT] append {line[:160]}", flush=True)
        return line

    def last_n(self, n: int = 10) -> str:
        with self._lock:
            items = list(self._turns)[-n:]
        return "\n".join(items)

    def interviewer_window(self, n: int = 3) -> str:
        with self._lock:
            items = list(self._interviewer)[-n:]
        return "\n".join(items)

    def latest_substantive_interviewer(self) -> str:
        with self._lock:
            items = list(self._interviewer)
        for text in reversed(items):
            if not looks_like_chitchat(text):
                return text
        return items[-1] if items else ""

    def all_text(self) -> str:
        with self._lock:
            return "\n".join(self._turns)

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()
            self._interviewer.clear()


def looks_like_technical_prompt(text: str) -> bool:
    return classify_intent(text) in ("coding", "technical_discussion") or bool(
        re.search(
            r"write |implement|\bcode\b|c\+\+|python|java|leetcode|algorithm|"
            r"linked list|insert |design |sql|function|optimize|debug",
            (text or "").lower(),
        )
    )


def looks_like_chitchat(text: str) -> bool:
    """Audio checks / greetings that should NOT burn a Gemini turn."""
    t = " ".join((text or "").strip().lower().split())
    if not t:
        return True
    if len(t) < 56 and any(
        p in t
        for p in (
            "hello",
            "hi there",
            "hey there",
            "good morning",
            "good afternoon",
            "can you hear",
            "could you hear",
            "are you there",
            "you there",
            "am i audible",
            "i'm audible",
            "im audible",
            "are you audible",
            "audible to you",
            "hear me",
            "hearing me",
            "testing 1",
            "test test",
            "check check",
            "mic check",
            "is this working",
            "you got me",
        )
    ):
        if looks_like_technical_prompt(t):
            return False
        return True
    return False


def looks_like_interviewer_prompt(text: str) -> bool:
    """True when an interviewer utterance should trigger Gemini (not only '?')."""
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if looks_like_chitchat(t):
        return False
    lower = t.lower()
    if "?" in t:
        return True
    triggers = (
        "write ",
        "implement",
        "code",
        "explain",
        "design",
        "how would",
        "what is",
        "what's",
        "can you",
        "could you",
        "please ",
        "solve",
        "create ",
        "build ",
        "tell me",
        "walk me",
        "describe",
        "optimize",
        "debug",
        "fix ",
        "insert ",
        "given ",
        "leetcode",
        "complexity",
    )
    return any(k in lower for k in triggers)


class AIOrchestrator(QObject):
    """Gemini streaming orchestrator with conversation memory + auto trigger."""

    mode_changed_signal = pyqtSignal(str)

    def __init__(self, hub: StreamHub | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = CONFIG.ai
        self.hub = hub
        self.memory = ConversationMemory(maxlen=40)
        self._client: Any = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._auto_enabled = True
        self._debounce_timer: threading.Timer | None = None
        self._on_error: Callable[[str], None] | None = None
        self._mode_provider: Callable[[], str] | None = None
        self._generation = 0
        self._pending_auto_text: str = ""
        self._active_mode: str = "auto"

    def set_hub(self, hub: StreamHub) -> None:
        self.hub = hub

    def set_error_handler(self, cb: Callable[[str], None]) -> None:
        self._on_error = cb

    def set_mode_provider(self, cb: Callable[[], str]) -> None:
        """UI supplies current Ask-mode combo (coding / auto / …)."""
        self._mode_provider = cb

    def _current_mode(self) -> str:
        if self._mode_provider is not None:
            try:
                mode = (self._mode_provider() or "auto").strip()
                if mode:
                    return mode
            except Exception:  # noqa: BLE001
                pass
        return self._active_mode or "auto"

    def _apply_classified_mode(self, classified: str) -> str:
        """Update active mode + notify UI dropdown when intent is non-auto."""
        if classified and classified != "auto":
            if classified != self._active_mode:
                print(f"[INTENT] mode → {classified}", flush=True)
            self._active_mode = classified
            try:
                self.mode_changed_signal.emit(classified)
            except Exception:  # noqa: BLE001
                log.exception("mode_changed_signal emit failed")
            return classified
        if self._active_mode in ("coding", "technical_discussion", "behavioral"):
            return self._active_mode
        return self._current_mode()

    def record_interviewer(self, text: str, auto_ask: bool = True) -> None:
        self.memory.append("[INTERVIEWER]", text)
        CTX.add_transcript("interviewer", text)

        window = self.memory.interviewer_window(3)
        classified = classify_intent(window) if window else classify_intent(text)
        if classified == "auto":
            classified = classify_intent(text)
        mode = self._apply_classified_mode(classified)

        if auto_ask and self._auto_enabled and looks_like_interviewer_prompt(text):
            self._schedule_auto_ask(text, mode=mode)
        elif auto_ask and looks_like_chitchat(text):
            print(
                f"[GEMINI STREAM START] skip chitchat/audio-check: {text[:80]!r}",
                flush=True,
            )

    def record_candidate(self, text: str) -> None:
        self.memory.append("[CANDIDATE]", text)
        CTX.add_transcript("candidate", text)

    def _schedule_auto_ask(self, latest: str, mode: str | None = None) -> None:
        """
        Buffer interviewer words; fire on '?' quickly or after ≥1.5s pause.
        Never drop a newer prompt while a prior stream is still running.
        """
        self._pending_auto_text = latest
        pending_mode = mode or self._active_mode or "auto"
        if self._debounce_timer is not None:
            self._debounce_timer.cancel()

        delay = 0.45 if "?" in latest else 1.5

        def _fire() -> None:
            text = self._pending_auto_text
            window = self.memory.interviewer_window(3)
            classified = classify_intent(window) if window else classify_intent(text)
            if classified == "auto":
                classified = classify_intent(text)
            resolved = self._apply_classified_mode(classified)
            if resolved == "auto":
                resolved = pending_mode if pending_mode != "auto" else self._current_mode()
            primary = self.memory.latest_substantive_interviewer() or text
            print(
                f"[GEMINI STREAM START] AUTO trigger "
                f"(mode={resolved}, delay={delay}, streaming={CTX.is_ai_streaming}): "
                f"{primary[:100]!r}",
                flush=True,
            )
            self.ask(
                user_hint=(
                    "SYSTEM_INSTRUCTION override: Ignore casual banter. "
                    "Answer the LATEST substantive [INTERVIEWER] request now with "
                    "optimized code / architecture / STAR as appropriate. "
                    f"Primary request: {primary}"
                ),
                mode=resolved,
                include_image=True,
                persist=True,
            )

        self._debounce_timer = threading.Timer(delay, _fire)
        self._debounce_timer.daemon = True
        self._debounce_timer.start()

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = CONFIG.google_api_key
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY / GEMINI_API_KEY is not configured")
        try:
            from google import genai

            self._client = ("genai", genai.Client(api_key=api_key))
            self.config.gemini_model = normalize_gemini_model(self.config.gemini_model)
            log.info("Using google-genai SDK model=%s", self.config.gemini_model)
            return self._client
        except Exception:  # noqa: BLE001
            log.debug("google-genai not available, trying google.generativeai")

        import google.generativeai as genai_legacy

        genai_legacy.configure(api_key=api_key)
        self.config.gemini_model = normalize_gemini_model(self.config.gemini_model)
        model = genai_legacy.GenerativeModel(
            model_name=self.config.gemini_model,
            system_instruction=_load_system_prompt(),
        )
        self._client = ("legacy", model)
        log.info("Using google-generativeai SDK model=%s", self.config.gemini_model)
        return self._client

    def build_prompt(
        self,
        user_hint: str = "",
        include_image: bool = True,
        mode: str = "auto",
    ) -> tuple[str, bytes | None]:
        history = self.memory.last_n(10)
        if not history:
            history = CTX.transcript_block(40) or "(none yet)"

        ocr_text = CTX.latest_ocr_text
        rag = CTX.rag_context
        resume = CTX.resume_summary
        jd = CTX.job_description

        key = prompt_mode_for(mode)
        mode_instruction = {
            "auto": "Infer the best response style from context — prefer code if a programming ask is present.",
            "coding": (
                "CODING MODE: Immediately output optimized code in Markdown fences, "
                "then complexity and brief implementation hints. No conversational filler."
            ),
            "system_design": (
                "TECHNICAL DISCUSSION MODE: Architecture, scalability, trade-offs, "
                "and a crisp ASCII/Markdown diagram. No mic-check small talk."
            ),
            "behavioral": "BEHAVIORAL MODE: Coach a STAR answer grounded in the resume only.",
            "debug": "DEBUG MODE: Diagnose the bug from OCR/code and propose a fix.",
        }.get(key, "Infer the best response style from context.")

        parts = [
            f"## Mode\n{mode_instruction}",
            "## Live Transcript (last 10 tagged turns)\n"
            "Tags are authoritative. [INTERVIEWER] = question/request. "
            "[CANDIDATE] = what the user already said.\n"
            "IMPORTANT: Answer ONLY the most recent substantive [INTERVIEWER] ask. "
            "Ignore earlier mic checks / greetings if a later coding or technical question exists.\nIf ANY programming request appears in the window, answer THAT — not prior banter.\n"
            f"{history}",
        ]
        if ocr_text:
            parts.append(f"## Screen OCR Region\n{ocr_text}")
        if resume:
            parts.append(f"## Candidate Resume Summary\n{resume}")
        if jd:
            parts.append(f"## Job Description\n{jd}")
        if rag:
            parts.append(f"## Retrieved Notes / Documents\n{rag}")
        if user_hint.strip():
            parts.append(f"## Candidate Request\n{user_hint.strip()}")
        else:
            parts.append(
                "## Candidate Request\n"
                "Answer the latest [INTERVIEWER] turn with interview-ready guidance."
            )

        image = CTX.latest_ocr_image if include_image and CTX.latest_ocr_image else None
        print(
            f"[CONTEXT] prompt built history_chars={len(history)} "
            f"resume={bool(resume)} ocr={bool(ocr_text)} image={image is not None}",
            flush=True,
        )
        return "\n\n".join(parts), image

    def ask(
        self,
        user_hint: str = "",
        mode: str = "auto",
        include_image: bool = True,
        persist: bool = True,
        rolling_context: str = "",
    ) -> None:
        if rolling_context:
            # Compatibility no-op — memory is canonical now
            pass

        with self._lock:
            self._generation += 1
            generation = self._generation
            self._cancel.set()  # nudge in-flight stream to stop ASAP
            old = self._thread

        # Do not block long — generation id makes stale streams inert
        if old is not None and old.is_alive() and old is not threading.current_thread():
            old.join(timeout=0.35)

        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run_stream,
            kwargs={
                "user_hint": user_hint,
                "mode": mode,
                "include_image": include_image,
                "persist": persist,
                "generation": generation,
            },
            name=f"AIOrchestrator-{generation}",
            daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _emit_error(self, message: str) -> None:
        print(f"[PIPELINE ERROR] {message}", flush=True)
        if self.hub:
            self.hub.ai_error.emit(message)
            self.hub.emit_status(message)
        if self._on_error:
            try:
                self._on_error(message)
            except Exception:  # noqa: BLE001
                pass
        BUS.publish(EventType.AI_ERROR, message=message)

    def _run_stream(
        self,
        user_hint: str,
        mode: str,
        include_image: bool,
        persist: bool,
        generation: int = 0,
    ) -> None:
        if generation != self._generation:
            print(f"[GEMINI STREAM START] skip stale generation={generation}", flush=True)
            return

        CTX.is_ai_streaming = True
        CTX.add_chat("assistant", "", streaming=True)
        BUS.publish(EventType.STATUS, message="Thinking…")
        if self.hub:
            self.hub.ai_started.emit()
            self.hub.emit_status("Thinking…")
        print(
            f"[GEMINI STREAM START] gen={generation} mode={mode} hint={user_hint[:80]!r}",
            flush=True,
        )

        t0 = time.perf_counter()
        full: list[str] = []
        try:
            if not CONFIG.google_api_key:
                raise RuntimeError("GOOGLE_API_KEY missing — set it in .env")

            prompt, image = self.build_prompt(user_hint, include_image, mode)
            print(
                f"[GEMINI STREAM START] calling generate_content_stream "
                f"gen={generation} model={self.config.gemini_model} prompt_chars={len(prompt)}",
                flush=True,
            )

            for token in self._stream_tokens(prompt, image):
                if generation != self._generation or self._cancel.is_set():
                    print(
                        f"[GEMINI STREAM START] cancelled gen={generation} "
                        f"current={self._generation}",
                        flush=True,
                    )
                    break
                full.append(token)
                CTX.append_assistant_token(token)
                if self.hub:
                    self.hub.emit_ai_chunk(token)
                BUS.publish(EventType.AI_TOKEN, token=token)

            # Stale streams must not paint over a newer answer
            if generation != self._generation:
                print(
                    f"[GEMINI STREAM START] discard stale complete gen={generation}",
                    flush=True,
                )
                return

            CTX.finalize_assistant()
            answer = "".join(full).strip()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.info("Gemini stream complete %.0fms (%d chars)", elapsed_ms, len(answer))
            print(
                f"[GEMINI STREAM START] complete gen={generation} chars={len(answer)} "
                f"latency_ms={elapsed_ms:.0f}",
                flush=True,
            )

            if self._cancel.is_set() and generation != self._generation:
                return

            if not answer:
                self._emit_error(
                    "Gemini returned empty response — check API key, model name, and quota"
                )
            else:
                if self.hub:
                    self.hub.ai_complete.emit(answer, elapsed_ms)
                    self.hub.emit_status(f"Answer ready ({elapsed_ms:.0f} ms)")
                BUS.publish(EventType.AI_COMPLETE, text=answer, latency_ms=elapsed_ms)
                BUS.publish(EventType.STATUS, message=f"Answer ready ({elapsed_ms:.0f} ms)")

            if persist and answer and CTX.session_id and generation == self._generation:
                try:
                    get_db().add_message(
                        CTX.session_id,
                        role="assistant",
                        content=answer,
                        source="ai",
                        meta={"mode": mode, "latency_ms": elapsed_ms, "generation": generation},
                    )
                except Exception:  # noqa: BLE001
                    log.exception("Failed to persist AI message")
        except Exception as exc:  # noqa: BLE001
            if generation != self._generation:
                return
            log.exception("AI orchestration failed")
            CTX.finalize_assistant()
            self._emit_error(f"Gemini error: {exc}")
            BUS.publish(EventType.STATUS, message=f"AI error: {exc}")
        finally:
            if generation == self._generation:
                CTX.is_ai_streaming = False

    def _stream_tokens(self, prompt: str, image_jpeg: bytes | None) -> Generator[str, None, None]:
        kind, client = self._ensure_client()
        if kind == "genai":
            yield from self._stream_google_genai(client, prompt, image_jpeg)
        else:
            yield from self._stream_legacy(client, prompt, image_jpeg)

    def _stream_google_genai(
        self, client: Any, prompt: str, image_jpeg: bytes | None
    ) -> Generator[str, None, None]:
        from google.genai import types

        parts: list[Any] = [types.Part.from_text(text=prompt)]
        if image_jpeg:
            parts.insert(0, types.Part.from_bytes(data=image_jpeg, mime_type="image/jpeg"))
        contents = [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            system_instruction=_load_system_prompt(),
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_output_tokens,
        )

        primary = normalize_gemini_model(self.config.gemini_model)
        candidates: list[str] = []
        for name in (primary, *GEMINI_MODEL_FALLBACKS):
            n = normalize_gemini_model(name)
            if n not in candidates:
                candidates.append(n)

        last_exc: Exception | None = None
        for model_name in candidates:
            try:
                print(f"[GEMINI STREAM START] model={model_name}", flush=True)
                response = client.models.generate_content_stream(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                # Commit working model so later turns skip dead ids
                if model_name != self.config.gemini_model:
                    log.info("Gemini model fallback engaged: %s → %s", self.config.gemini_model, model_name)
                    self.config.gemini_model = model_name
                for chunk in response:
                    if self._cancel.is_set():
                        break
                    text = getattr(chunk, "text", None)
                    if text:
                        yield text
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc)
                not_found = "404" in msg or "NOT_FOUND" in msg or "not found" in msg.lower()
                print(f"[GEMINI STREAM START] model={model_name} failed: {exc}", flush=True)
                if not_found:
                    continue
                raise RuntimeError(f"generate_content_stream failed: {exc}") from exc

        raise RuntimeError(f"generate_content_stream failed: {last_exc}") from last_exc

    def _stream_legacy(
        self, model: Any, prompt: str, image_jpeg: bytes | None
    ) -> Generator[str, None, None]:
        content: list[Any] = [prompt]
        if image_jpeg:
            from PIL import Image
            import io

            content.insert(0, Image.open(io.BytesIO(image_jpeg)))

        # Legacy path: recreate GenerativeModel with fallbacks on 404
        import google.generativeai as genai_legacy

        primary = normalize_gemini_model(self.config.gemini_model)
        candidates: list[str] = []
        for name in (primary, *GEMINI_MODEL_FALLBACKS):
            n = normalize_gemini_model(name)
            if n not in candidates:
                candidates.append(n)

        last_exc: Exception | None = None
        for model_name in candidates:
            try:
                active = model
                if normalize_gemini_model(getattr(model, "model_name", "") or "") != model_name:
                    active = genai_legacy.GenerativeModel(
                        model_name=model_name,
                        system_instruction=_load_system_prompt(),
                    )
                stream = active.generate_content(
                    content,
                    stream=True,
                    generation_config={
                        "temperature": self.config.temperature,
                        "max_output_tokens": self.config.max_output_tokens,
                    },
                )
                if model_name != self.config.gemini_model:
                    self.config.gemini_model = model_name
                    self._client = ("legacy", active)
                for chunk in stream:
                    if self._cancel.is_set():
                        break
                    try:
                        text = chunk.text
                    except Exception:  # noqa: BLE001
                        text = ""
                    if text:
                        yield text
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc)
                not_found = "404" in msg or "NOT_FOUND" in msg or "not found" in msg.lower()
                if not_found:
                    continue
                raise RuntimeError(f"legacy generate_content failed: {exc}") from exc

        raise RuntimeError(f"legacy generate_content failed: {last_exc}") from last_exc

    def ask_sync(self, user_hint: str = "", mode: str = "auto") -> str:
        prompt, image = self.build_prompt(user_hint, True, mode)
        return "".join(self._stream_tokens(prompt, image))
