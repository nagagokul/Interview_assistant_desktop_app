"""
OCRRegionService — continuous region capture with differential pixel checks.

Uses mss (cross-platform) or dxcam (Windows DXCAM) for near-0ms grabs,
then Tesseract for text extraction only when the region actually changes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from src.core.config import CONFIG
from src.core.context import CTX
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger
from src.core.paths import tesseract_executable
from src.utils.image_diff import DiffResult, encode_jpeg, pixel_change_ratio

log = get_logger("ocr")


class ScreenGrabber:
    """Prefer dxcam on Windows; fall back to mss."""

    def __init__(self) -> None:
        self._backend = "none"
        self._camera: Any = None
        self._sct: Any = None
        self._init_backend()

    def _init_backend(self) -> None:
        try:
            import dxcam  # type: ignore

            self._camera = dxcam.create(output_idx=0, output_color="BGR")
            self._backend = "dxcam"
            log.info("Screen grabber: dxcam")
            return
        except Exception:  # noqa: BLE001
            log.debug("dxcam unavailable, trying mss")
        try:
            import mss

            self._sct = mss.mss()
            self._backend = "mss"
            log.info("Screen grabber: mss")
        except Exception:  # noqa: BLE001
            log.exception("No screen capture backend available")
            self._backend = "none"

    @property
    def backend(self) -> str:
        return self._backend

    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray | None:
        """region = (left, top, right, bottom) in virtual-screen coords."""
        left, top, right, bottom = region
        width = max(1, right - left)
        height = max(1, bottom - top)
        if self._backend == "dxcam" and self._camera is not None:
            frame = self._camera.grab(region=(left, top, right, bottom))
            if frame is None:
                return None
            return np.asarray(frame)
        if self._backend == "mss" and self._sct is not None:
            monitor = {"left": left, "top": top, "width": width, "height": height}
            shot = self._sct.grab(monitor)
            # BGRA
            return np.asarray(shot)
        return None

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:  # noqa: BLE001
                pass
            self._sct = None


class TesseractOCR:
    def __init__(self, lang: str = "eng", psm: int = 6) -> None:
        self.lang = lang
        self.psm = psm
        self._ready = False
        self._configure()

    def _configure(self) -> None:
        try:
            import pytesseract

            exe = tesseract_executable()
            if exe is not None:
                pytesseract.pytesseract.tesseract_cmd = str(exe)
                log.info("Tesseract binary: %s", exe)
            # Probe
            _ = pytesseract.get_tesseract_version()
            self._ready = True
        except Exception:  # noqa: BLE001
            log.warning("Tesseract not ready — OCR will return empty until installed")
            self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def extract(self, frame: np.ndarray) -> str:
        if not self._ready:
            return ""
        import pytesseract
        from PIL import Image

        arr = frame
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[:, :, :3]
        if arr.ndim == 3 and arr.shape[2] == 3:
            rgb = arr[:, :, ::-1]
        else:
            rgb = arr
        img = Image.fromarray(rgb.astype(np.uint8))
        config = f"--psm {self.psm} -l {self.lang}"
        text = pytesseract.image_to_string(img, config=config)
        return (text or "").strip()


class OCRRegionService:
    """Background region watcher with differential OCR triggering."""

    def __init__(self) -> None:
        self.config = CONFIG.ocr
        self.grabber = ScreenGrabber()
        self.ocr = TesseractOCR(self.config.tesseract_lang, self.config.tesseract_psm)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._prev_frame: np.ndarray | None = None
        self._last_text = ""
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def set_region(self, region: tuple[int, int, int, int] | None) -> None:
        CTX.set_region(region)
        self._prev_frame = None
        BUS.publish(EventType.REGION_SET, region=region)
        if region:
            log.info("OCR region set to %s", region)

    def start(self) -> None:
        if self._running:
            return
        if CTX.ocr_region is None:
            BUS.publish(EventType.STATUS, message="Select an OCR region first (Alt+S)")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="OCRRegion", daemon=True)
        self._thread.start()
        self._running = True
        CTX.is_ocr_running = True
        BUS.publish(EventType.STATUS, message="OCR region watching")
        log.info("OCRRegionService started backend=%s", self.grabber.backend)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._running = False
        CTX.is_ocr_running = False
        self.grabber.close()
        BUS.publish(EventType.STATUS, message="OCR stopped")
        log.info("OCRRegionService stopped")

    def capture_once(self) -> str:
        region = CTX.ocr_region
        if not region:
            return ""
        frame = self.grabber.grab(region)
        if frame is None:
            return ""
        return self._process_frame(frame, force=True)

    def _loop(self) -> None:
        interval = max(20, self.config.poll_interval_ms) / 1000.0
        while not self._stop.is_set():
            t0 = time.perf_counter()
            region = CTX.ocr_region
            if region:
                frame = self.grabber.grab(region)
                if frame is not None:
                    self._process_frame(frame, force=False)
            elapsed = time.perf_counter() - t0
            sleep_for = max(0.0, interval - elapsed)
            self._stop.wait(sleep_for)

    def _process_frame(self, frame: np.ndarray, force: bool) -> str:
        # Clamp oversized regions for OCR cost control
        h, w = frame.shape[:2]
        if w > self.config.max_region_width or h > self.config.max_region_height:
            scale = min(
                self.config.max_region_width / max(w, 1),
                self.config.max_region_height / max(h, 1),
            )
            new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
            ys = (np.linspace(0, h - 1, new_h)).astype(np.int32)
            xs = (np.linspace(0, w - 1, new_w)).astype(np.int32)
            frame = frame[ys][:, xs]

        diff: DiffResult = pixel_change_ratio(
            self._prev_frame,
            frame,
            threshold=self.config.change_threshold,
        )
        if not force and not diff.changed:
            return self._last_text

        t0 = time.perf_counter()
        text = self.ocr.extract(frame)
        jpeg = encode_jpeg(frame, self.config.jpeg_quality)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._prev_frame = frame.copy()
        if text and text != self._last_text:
            self._last_text = text
            CTX.set_ocr(text, jpeg)
            BUS.publish(EventType.OCR_TEXT, text=text, change_ratio=diff.change_ratio)
            print(f"[UI ROUTE] OCR_TEXT chars={len(text)} change={diff.change_ratio:.3f}", flush=True)
            log.info(
                "OCR %.0fms change=%.3f chars=%d",
                elapsed_ms,
                diff.change_ratio,
                len(text),
            )
        elif force:
            CTX.set_ocr(text, jpeg)
        return text
