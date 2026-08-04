"""
AIOrchestrator — Gemini 1.5 Flash streaming into StreamHub.ai_chunk signals.

Stitches system instructions, resume/RAG buffers, live transcripts, OCR text,
and optional region images. Worker-thread stream → QueuedConnection → GUI.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Generator

from src.core.config import CONFIG
from src.core.context import CTX
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger
from src.core.paths import prompts_dir
from src.data.database import get_db

if TYPE_CHECKING:
    from src.core.stream_hub import StreamHub

log = get_logger("ai")

DEFAULT_SYSTEM_PROMPT = """You are Interview Copilot — an elite, discreet technical interview assistant.

Mission: Deliver nearly instant, high-signal help during coding interviews, system design,
debugging, and behavioral (STAR) conversations.

Rules:
1. Be concise. Prefer short, interview-ready answers over essays.
2. Match the language the interviewer is using (C, C++, Python, Java, SQL, Bash, etc.).
3. For coding: provide correct, optimized solutions with time/space complexity.
4. For system design: give clear components, trade-offs, and a crisp diagram in ASCII/Markdown.
5. For behavioral: use STAR (Situation, Task, Action, Result) grounded in the candidate's resume.
6. Generate likely follow-up questions the interviewer may ask next.
7. Never invent resume facts — use only provided resume/RAG context.
8. If screen OCR shows a problem statement or code, prioritize that as the question.
9. Output structure when helpful:
   - Answer
   - Complexity (if coding)
   - Follow-ups
10. Stay calm, professional, and high-signal under time pressure.
"""


def _load_system_prompt() -> str:
    path = prompts_dir() / CONFIG.ai.system_prompt_file
    if path.is_file():
        return path.read_text(encoding="utf-8")
    try:
        prompts_dir().mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_SYSTEM_PROMPT, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_SYSTEM_PROMPT


class AIOrchestrator:
    """Gemini streaming orchestrator with multimodal (text + image) support."""

    def __init__(self, hub: StreamHub | None = None) -> None:
        self.config = CONFIG.ai
        self.hub = hub
        self._client: Any = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._extra_context: str = ""

    def set_hub(self, hub: StreamHub) -> None:
        self.hub = hub

    def set_rolling_context(self, context: str) -> None:
        """Inject audio service rolling transcript string into the next prompt."""
        self._extra_context = context or ""

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = CONFIG.google_api_key
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY / GEMINI_API_KEY is not configured")

        try:
            from google import genai

            self._client = ("genai", genai.Client(api_key=api_key))
            log.info("Using google-genai SDK model=%s", self.config.gemini_model)
            return self._client
        except Exception:  # noqa: BLE001
            log.debug("google-genai not available, trying google.generativeai")

        import google.generativeai as genai_legacy

        genai_legacy.configure(api_key=api_key)
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
        transcript = CTX.transcript_block(40)
        if self._extra_context:
            transcript = (transcript + "\n" + self._extra_context).strip()
        ocr_text = CTX.latest_ocr_text
        rag = CTX.rag_context
        resume = CTX.resume_summary
        jd = CTX.job_description

        mode_instruction = {
            "auto": "Infer the best response style from context.",
            "coding": "Focus on correct optimized code, edge cases, and complexity.",
            "system_design": "Focus on architecture, scalability, and trade-offs.",
            "behavioral": "Coach a STAR answer grounded in the resume.",
            "debug": "Diagnose the bug from OCR/code and propose a fix.",
        }.get(mode, "Infer the best response style from context.")

        parts = [
            f"## Mode\n{mode_instruction}",
            f"## Live Transcript (interviewer vs candidate)\n{transcript or '(none yet)'}",
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
                "Provide the best next answer / code / talking points for the latest interviewer question."
            )

        image = CTX.latest_ocr_image if include_image and CTX.latest_ocr_image else None
        return "\n\n".join(parts), image

    def ask(
        self,
        user_hint: str = "",
        mode: str = "auto",
        include_image: bool = True,
        persist: bool = True,
        rolling_context: str = "",
    ) -> None:
        """Fire-and-forget streaming ask on a worker thread (keeps UI fluid)."""
        if rolling_context:
            self._extra_context = rolling_context
        if self._thread and self._thread.is_alive():
            self.cancel()
            self._thread.join(timeout=1.0)

        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run_stream,
            kwargs={
                "user_hint": user_hint,
                "mode": mode,
                "include_image": include_image,
                "persist": persist,
            },
            name="AIOrchestrator",
            daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run_stream(
        self,
        user_hint: str,
        mode: str,
        include_image: bool,
        persist: bool,
    ) -> None:
        CTX.is_ai_streaming = True
        CTX.add_chat("assistant", "", streaming=True)
        BUS.publish(EventType.STATUS, message="Thinking…")
        if self.hub:
            self.hub.ai_started.emit()
            self.hub.emit_status("Thinking…")
        print("[GEMINI STREAM START] mode=%s hint=%r" % (mode, user_hint[:80]), flush=True)

        t0 = time.perf_counter()
        full: list[str] = []
        try:
            prompt, image = self.build_prompt(user_hint, include_image, mode)
            print(f"[GEMINI STREAM START] prompt_chars={len(prompt)} has_image={image is not None}", flush=True)

            for token in self._stream_tokens(prompt, image):
                if self._cancel.is_set():
                    print("[GEMINI STREAM START] cancelled", flush=True)
                    break
                full.append(token)
                CTX.append_assistant_token(token)
                # Thread-safe Qt signal (primary)
                if self.hub:
                    self.hub.emit_ai_chunk(token)
                BUS.publish(EventType.AI_TOKEN, token=token)

            CTX.finalize_assistant()
            answer = "".join(full).strip()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.info("Gemini stream complete %.0fms (%d chars)", elapsed_ms, len(answer))
            print(f"[GEMINI STREAM START] complete chars={len(answer)} latency_ms={elapsed_ms:.0f}", flush=True)

            if self.hub:
                self.hub.ai_complete.emit(answer, elapsed_ms)
                self.hub.emit_status(f"Answer ready ({elapsed_ms:.0f} ms)")
            BUS.publish(EventType.AI_COMPLETE, text=answer, latency_ms=elapsed_ms)
            BUS.publish(EventType.STATUS, message=f"Answer ready ({elapsed_ms:.0f} ms)")

            if persist and answer and CTX.session_id:
                try:
                    get_db().add_message(
                        CTX.session_id,
                        role="assistant",
                        content=answer,
                        source="ai",
                        meta={"mode": mode, "latency_ms": elapsed_ms},
                    )
                    if user_hint:
                        get_db().add_message(
                            CTX.session_id,
                            role="user",
                            content=user_hint,
                            source="manual",
                        )
                except Exception:  # noqa: BLE001
                    log.exception("Failed to persist AI message")
        except Exception as exc:  # noqa: BLE001
            log.exception("AI orchestration failed")
            print(f"[GEMINI STREAM START] ERROR {exc}", flush=True)
            CTX.finalize_assistant()
            if self.hub:
                self.hub.ai_error.emit(str(exc))
                self.hub.emit_status(f"AI error: {exc}")
            BUS.publish(EventType.AI_ERROR, message=str(exc))
            BUS.publish(EventType.STATUS, message=f"AI error: {exc}")
        finally:
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
            parts.insert(
                0,
                types.Part.from_bytes(data=image_jpeg, mime_type="image/jpeg"),
            )
        contents = [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            system_instruction=_load_system_prompt(),
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_output_tokens,
        )
        # Official google-genai streaming loop
        response = client.models.generate_content_stream(
            model=self.config.gemini_model,
            contents=contents,
            config=config,
        )
        for chunk in response:
            if self._cancel.is_set():
                break
            text = getattr(chunk, "text", None)
            if text:
                yield text

    def _stream_legacy(
        self, model: Any, prompt: str, image_jpeg: bytes | None
    ) -> Generator[str, None, None]:
        content: list[Any] = [prompt]
        if image_jpeg:
            from PIL import Image
            import io

            content.insert(0, Image.open(io.BytesIO(image_jpeg)))
        stream = model.generate_content(
            content,
            stream=True,
            generation_config={
                "temperature": self.config.temperature,
                "max_output_tokens": self.config.max_output_tokens,
            },
        )
        for chunk in stream:
            if self._cancel.is_set():
                break
            try:
                text = chunk.text
            except Exception:  # noqa: BLE001
                text = ""
            if text:
                yield text

    def ask_sync(self, user_hint: str = "", mode: str = "auto") -> str:
        prompt, image = self.build_prompt(user_hint, True, mode)
        return "".join(self._stream_tokens(prompt, image))
