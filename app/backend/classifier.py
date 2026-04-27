"""PDF classification (F-02)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import fitz

from .ocr import needs_ocr, ocr_page


PDF_KIND_BULK_DETAIL = "一括納付用明細書情報"
PDF_KIND_PAYMENT_NOTICE = "納付番号通知情報"
PDF_KIND_COVER_LETTER = "書類送付案内状"
PDF_KIND_PERMIT = "輸入許可通知書"
PDF_KIND_FREIGHT_INVOICE = "諸掛請求書"
PDF_KIND_DEFERRED_MULTI = "延納マルチ納付書"
PDF_KIND_BULK_BUNDLE = "一括納付明細書(複合)"  # 案内状+納付通知+明細を1本のPDFにまとめたもの
PDF_KIND_UNKNOWN = "unknown"


@dataclass
class PageClassification:
    page_index: int
    kind: str
    text: str


@dataclass
class ClassifiedDocument:
    filename: str
    file_path: str
    overall_kind: str
    page_count: int
    pages: List[PageClassification]


def _page_kind(text: str) -> str:
    if "書類送付案内状" in text:
        return PDF_KIND_COVER_LETTER
    if "納付番号通知情報" in text:
        return PDF_KIND_PAYMENT_NOTICE
    if "一括納付用明細書情報" in text:
        return PDF_KIND_BULK_DETAIL
    # 輸入許可通知書: header text or OCR fallback
    if "輸入許可通知書" in text:
        return PDF_KIND_PERMIT
    # 表記ゆれ・OCR誤認: <SEA/IMP> <AIR/IMP> / SEA/1MP / AIR/1MP
    upper = text.upper()
    if any(tag in upper for tag in ("SEA/IMP", "AIR/IMP", "SEA/1MP", "AIR/1MP")):
        return PDF_KIND_PERMIT
    if "請求書" in text and ("TRADIA" in upper or "トレーディア" in text):
        return PDF_KIND_FREIGHT_INVOICE
    return PDF_KIND_UNKNOWN


def _overall_kind(filename: str, kinds: List[str]) -> str:
    name = filename
    name_upper = name.upper()
    has_permit = PDF_KIND_PERMIT in kinds
    has_invoice = PDF_KIND_FREIGHT_INVOICE in kinds
    has_payment = PDF_KIND_PAYMENT_NOTICE in kinds
    has_detail = PDF_KIND_BULK_DETAIL in kinds
    has_cover = PDF_KIND_COVER_LETTER in kinds

    # Content-first classification: if (post-OCR) pages contain bundle
    # content, treat the document as a bundle even when the filename says
    # 「延納マルチ」 (which only describes the SOURCE format).
    if has_invoice or "諸掛請求書" in name:
        return PDF_KIND_FREIGHT_INVOICE

    if has_payment or has_detail or has_cover:
        return PDF_KIND_BULK_BUNDLE

    if "延納マルチ" in name:
        return PDF_KIND_DEFERRED_MULTI

    if has_permit or "輸入許可通知書" in name or "輸入許可書" in name:
        return PDF_KIND_PERMIT

    # ファイル名フォールバック
    if "一括納付明細書" in name or "一括納付" in name:
        return PDF_KIND_BULK_BUNDLE
    if name_upper.endswith(".PDF") and any(c.isdigit() for c in name) and len(name) >= 11:
        return PDF_KIND_BULK_BUNDLE
    return PDF_KIND_UNKNOWN


def classify_pdf(filename: str, file_path: str, *, enable_ocr: bool = True) -> ClassifiedDocument:
    """Open *file_path* with PyMuPDF and classify pages and overall document.

    If ``enable_ocr`` is True (default) and a page has no embedded text,
    Tesseract is invoked to extract text from the rasterised page image.
    """
    doc = fitz.open(file_path)
    pages: List[PageClassification] = []
    for i, page in enumerate(doc):
        text = page.get_text() or ""
        if enable_ocr and needs_ocr(page):
            ocr_text = ocr_page(page)
            if ocr_text:
                text = ocr_text
        pages.append(PageClassification(page_index=i, kind=_page_kind(text), text=text))
    page_count = len(doc)
    doc.close()
    kinds = [p.kind for p in pages]
    overall = _overall_kind(filename, kinds)
    return ClassifiedDocument(
        filename=filename,
        file_path=file_path,
        overall_kind=overall,
        page_count=page_count,
        pages=pages,
    )
