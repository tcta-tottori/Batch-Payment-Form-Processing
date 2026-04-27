"""OCR adapter (F-11).

Renders image-based PDF pages with PyMuPDF and runs Tesseract for text
extraction. Falls back to a no-op if Tesseract is not installed.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
from typing import Optional

import fitz

LOGGER = logging.getLogger(__name__)


# Detection threshold: a page with embedded images and effectively no text
# (after stripping whitespace) is treated as a scanned page.
_OCR_TEXT_THRESHOLD = 8


def _tesseract_available() -> bool:
    if shutil.which("tesseract"):
        return True
    return False


_TESSERACT_OK: Optional[bool] = None


def tesseract_ready() -> bool:
    global _TESSERACT_OK
    if _TESSERACT_OK is None:
        _TESSERACT_OK = _tesseract_available()
        if not _TESSERACT_OK:
            LOGGER.warning("Tesseract not found; OCR disabled.")
    return _TESSERACT_OK


def needs_ocr(page: "fitz.Page") -> bool:
    """Heuristic: image-based pages have images and almost no text."""
    text = (page.get_text() or "").strip()
    if len(text) >= _OCR_TEXT_THRESHOLD:
        return False
    try:
        images = page.get_images()
    except Exception:
        images = []
    return bool(images)


def ocr_page(page: "fitz.Page", lang: str = "jpn+eng", dpi: int = 300) -> str:
    """Render *page* as an image and OCR it. Returns "" on any failure."""
    if not tesseract_ready():
        return ""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        LOGGER.warning("pytesseract / Pillow not installed; OCR disabled.")
        return ""

    try:
        pix = page.get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
        img = Image.open(io.BytesIO(png))
        text = pytesseract.image_to_string(img, lang=lang)
        return text or ""
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("OCR failed: %s", exc)
        return ""
