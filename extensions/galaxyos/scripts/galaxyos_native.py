#!/usr/bin/env python3
"""
galaxyos_native — Pure-Python shim (embedded fallback for Rust PyO3 extension)

Provides the same API as the compiled galaxyos_native Rust module:
  resize, enhance, ocr_preprocess, vector_dot, vector_cosine, vector_batch_cosine

When the real compiled .so is available, it takes precedence over this shim.
This ensures import succeeds and all ops work via PIL/numpy without requiring
a Rust toolchain at deploy time.
"""

__version__ = "0.1.0"
__doc__ = "GalaxyOS native extension — PIL replacement + SIMD vector compute (pure-Python fallback)"
_BACKEND = "python"  # "rust" when compiled PyO3 .so; "python" when shim

import math
import base64
import io
from typing import List

try:
    from PIL import Image, ImageEnhance, ImageFilter
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


def _load_image(data: bytes):
    if not _PIL_AVAILABLE:
        raise RuntimeError("PIL not available")
    img = Image.open(io.BytesIO(data))
    if img.mode == "RGBA":
        img = img.convert("RGB")
    return img


def _encode_image(img: Image.Image, fmt: str) -> str:
    buf = io.BytesIO()
    fmt_lower = fmt.lower()
    save_fmt = fmt_lower.upper() if fmt_lower in ("jpeg", "png", "webp") else "JPEG"
    img.save(buf, format=save_fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ── Image ops ──────────────────────────────────────────────────

def resize(data: bytes, width: int, height: int, keep_ratio: bool = True, fmt: str = "jpeg"):
    """Resize image. Returns dict with data_b64 and size."""
    img = _load_image(data)
    orig_w, orig_h = img.size
    if keep_ratio:
        ratio = min(width / orig_w, height / orig_h)
        new_w, new_h = int(orig_w * ratio), int(orig_h * ratio)
    else:
        new_w, new_h = width, height
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    b64 = _encode_image(resized, fmt)
    return {"data_b64": b64, "size": [new_w, new_h]}


def enhance(data: bytes, brightness: float = 1.0, contrast: float = 1.0, sharpness: float = 1.0, fmt: str = "jpeg"):
    """Enhance image. Returns base64-encoded string."""
    img = _load_image(data)
    if abs(brightness - 1.0) > 0.001:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if abs(contrast - 1.0) > 0.001:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if abs(sharpness - 1.0) > 0.001:
        # Simulate sharpness via unsharp mask
        img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=int((sharpness - 1.0) * 100), threshold=0))
    return _encode_image(img, fmt)


def ocr_preprocess(data: bytes, fmt: str = "png"):
    """OCR preprocess: grayscale + denoise + contrast enhance. Returns base64."""
    img = _load_image(data)
    gray = img.convert("L")
    denoised = gray.filter(ImageFilter.GaussianBlur(0.8))
    enhanced = ImageEnhance.Contrast(denoised).enhance(1.5)
    return _encode_image(enhanced, fmt)


# ── Vector ops ─────────────────────────────────────────────────

def vector_dot(a: List[float], b: List[float]) -> float:
    """Dot product of two float vectors."""
    if len(a) != len(b):
        raise RuntimeError(f"dimension mismatch: {len(a)} vs {len(b)}")
    return sum(x * y for x, y in zip(a, b))


def vector_cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity of two float vectors."""
    if len(a) != len(b):
        raise RuntimeError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a > 0 and norm_b > 0:
        return dot / (norm_a * norm_b)
    return 0.0


def vector_batch_cosine(query: List[float], candidates: List[List[float]]) -> List[float]:
    """Batch cosine similarity: query vs each candidate."""
    return [vector_cosine(query, c) for c in candidates]
