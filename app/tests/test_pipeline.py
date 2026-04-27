"""End-to-end pipeline test using the bundled sample PDFs."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.backend.classifier import classify_pdf
from app.backend.formatters import to_full_tsv, to_markdown
from app.backend.orchestrator import process_classified_documents


SAMPLES = [
    "一括納付明細書2026.03.pdf",
    "許可通知書_抽出_202603_トレーディア.pdf",
]
SAMPLES_WITH_OCR = SAMPLES + ["延納マルチ納付書 2026.3月分.pdf"]


def _load_docs(samples=SAMPLES):
    docs = []
    for f in samples:
        path = ROOT / f
        assert path.exists(), f"Sample missing: {path}"
        docs.append(classify_pdf(str(path.name), str(path)))
    return docs


def test_classifier_recognises_each_sample():
    docs = _load_docs()
    kinds = {d.filename: d.overall_kind for d in docs}
    assert kinds["一括納付明細書2026.03.pdf"] == "一括納付明細書(複合)"
    assert kinds["許可通知書_抽出_202603_トレーディア.pdf"] == "輸入許可通知書"


def test_pipeline_produces_expected_totals():
    cl = process_classified_documents(_load_docs())
    # Spec sample: 14 permits / 32 pages, total tax ¥11,547,900.
    permit_pages = sum(c.total_permit_pages for c in cl.companies)
    permit_count = sum(len(c.permits) for c in cl.companies)
    line_total = sum(it.amount for c in cl.companies for pd in c.details for it in pd.items)
    payment_total = sum(pd.total_amount for c in cl.companies for pd in c.details)
    assert permit_count == 14, f"expected 14 permits, got {permit_count}"
    assert permit_pages == 32, f"expected 32 permit pages, got {permit_pages}"
    assert line_total == 11_547_900, f"line item total mismatch: {line_total}"
    assert payment_total == 11_547_900, f"payment total mismatch: {payment_total}"


def test_validations_all_match():
    cl = process_classified_documents(_load_docs())
    # Per spec sample: 全7件 一致. We emit 2 validations per (BPN, subject)
    # pair (detail_vs_payment + declared_vs_payment) so expect 14 ✓.
    failed = [v for v in cl.validations if not v.match]
    assert not failed, f"unexpected validation failures: {failed}"


def test_outputs_render_without_exceptions():
    cl = process_classified_documents(_load_docs())
    md = to_markdown(cl)
    tsv = to_full_tsv(cl)
    assert "一括納付明細書チェックリスト" in md
    assert "添付PDFファイル名" in tsv
    # The 9-row 消費・地方消費税 detail must appear in the output
    assert "1,859,800" in md or "1859800" in tsv


def test_ocr_pipeline_includes_image_pdf():
    """F-11: 延納マルチ納付書 (image-only PDF) is OCR'd, parsed, and validated."""
    from app.backend.ocr import tesseract_ready
    if not tesseract_ready():
        # Skip when running on environments without Tesseract installed.
        return
    cl = process_classified_documents(_load_docs(SAMPLES_WITH_OCR))
    company_names = {c.name for c in cl.companies}
    assert "株式会社上組" in company_names, f"got: {company_names}"
    kamigumi = next(c for c in cl.companies if c.name == "株式会社上組")
    assert kamigumi.manual_permit_check is True
    # Two PaymentDetail entries (関税 + 消費・地方消費税)
    assert len(kamigumi.details) == 2
    subjects = {pd.subject_name for pd in kamigumi.details}
    assert subjects == {"関税", "消費・地方消費税"}
    # Adding the OCR'd file should add ¥1,200 + ¥2,064,300 = ¥2,065,500 to
    # the reconciled total.
    total = sum(pd.total_amount for c in cl.companies for pd in c.details)
    assert total == 11_547_900 + 1_200 + 2_064_300, total
    # All validations should still match.
    failed = [v for v in cl.validations if not v.match]
    assert not failed, f"unexpected validation failures: {failed}"


if __name__ == "__main__":
    test_classifier_recognises_each_sample()
    test_pipeline_produces_expected_totals()
    test_validations_all_match()
    test_outputs_render_without_exceptions()
    test_ocr_pipeline_includes_image_pdf()
    print("All tests passed.")
