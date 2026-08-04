"""High-speed frame differencing for OCR region change detection."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class DiffResult:
    changed: bool
    change_ratio: float
    mean_abs_diff: float


def to_gray_u8(frame: np.ndarray) -> np.ndarray:
    """Convert BGR/BGRA/RGB/RGBA/gray to uint8 grayscale."""
    if frame.ndim == 2:
        return frame.astype(np.uint8, copy=False)
    if frame.ndim == 3:
        channels = frame.shape[2]
        if channels == 4:
            # Prefer BGRA (mss / dxcam on Windows) luminance
            b, g, r = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
        elif channels == 3:
            b, g, r = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
        else:
            return frame[:, :, 0].astype(np.uint8, copy=False)
        # Integer luma approximation (BT.601)
        gray = (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)
        return gray
    raise ValueError(f"Unsupported frame shape: {frame.shape}")


def downsample(gray: np.ndarray, max_side: int = 640) -> np.ndarray:
    h, w = gray.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale >= 0.999:
        return gray
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    # Nearest-neighbor via slicing strides (fast, no OpenCV required)
    y_idx = (np.linspace(0, h - 1, new_h)).astype(np.int32)
    x_idx = (np.linspace(0, w - 1, new_w)).astype(np.int32)
    return gray[y_idx][:, x_idx]


def pixel_change_ratio(
    prev: np.ndarray | None,
    current: np.ndarray,
    pixel_delta: int = 18,
    threshold: float = 0.018,
) -> DiffResult:
    """
    Compare two frames; return whether enough pixels changed to warrant OCR.

    Target: <100ms including capture on i5 — uses grayscale + downsample.
    """
    cur = downsample(to_gray_u8(current))
    if prev is None:
        return DiffResult(changed=True, change_ratio=1.0, mean_abs_diff=255.0)

    prev_g = downsample(to_gray_u8(prev))
    if prev_g.shape != cur.shape:
        return DiffResult(changed=True, change_ratio=1.0, mean_abs_diff=255.0)

    diff = np.abs(cur.astype(np.int16) - prev_g.astype(np.int16))
    changed_mask = diff > pixel_delta
    ratio = float(np.mean(changed_mask))
    mad = float(np.mean(diff))
    return DiffResult(changed=ratio >= threshold, change_ratio=ratio, mean_abs_diff=mad)


def encode_jpeg(frame: np.ndarray, quality: int = 85) -> bytes:
    """Encode BGR/RGB numpy frame as JPEG bytes (Pillow)."""
    from io import BytesIO

    from PIL import Image

    arr = frame
    if arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]
    # mss returns BGRA; convert to RGB for Pillow
    if arr.ndim == 3 and arr.shape[2] == 3:
        rgb = arr[:, :, ::-1].copy()
    else:
        rgb = arr
    img = Image.fromarray(rgb.astype(np.uint8))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
