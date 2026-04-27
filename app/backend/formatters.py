"""Output formatters: Markdown / TSV (F-06, F-07)."""
from __future__ import annotations

from typing import List

from .models import (
    Company,
    InvoiceMapping,
    InvoiceMappingItem,
    MonthlyChecklist,
    PaymentDetail,
)


_INVOICE_STATUS_LABEL = {
    "matched": "",
    "not_recorded": "(記載なし)",
    "unmatched": "(未特定)",
    "not_in_invoice": "(当月請求書に未掲載)",
}


def _yen(amount: int) -> str:
    return f"¥{amount:,}"


def to_markdown(checklist: MonthlyChecklist) -> str:
    lines: List[str] = []
    lines.append("# 一括納付明細書チェックリスト")
    lines.append("")
    lines.append(f"- 対象月: {checklist.month or '(未判定)'}")
    lines.append(f"- 納期限: {checklist.deadline or '(未判定)'}")
    lines.append(f"- 作成日: {checklist.created_at}")
    lines.append("")

    # Section 1: アップロード PDF 一覧
    lines.append("## 添付PDFファイル名")
    lines.append("")
    lines.append("| # | 種別 | ファイル名 | ページ数 |")
    lines.append("|---|------|-----------|---------|")
    for i, uf in enumerate(checklist.uploaded_files, 1):
        lines.append(f"| {i} | {uf.pdf_kind} | {uf.filename} | {uf.pages} |")
    lines.append("")

    # Section 2: 各会社PDF枚数確認
    lines.append("## 各会社PDF枚数確認")
    lines.append("")
    lines.append("| # | 会社名 | 表紙枚数 | 許可通知書枚数 | 印刷枚数 | チェック | 備考 |")
    lines.append("|---|--------|---------|---------------|---------|---------|------|")
    for i, c in enumerate(checklist.companies, 1):
        cover = c.total_cover_pages
        permit = c.total_permit_pages
        total = cover + permit
        check = "□"
        note = "(手動確認)" if c.manual_permit_check else ""
        lines.append(
            f"| {i} | {c.name} | {cover} | {permit} | {total} | {check} | {note} |"
        )
    lines.append("")

    # Section 3: 各会社詳細情報
    lines.append("## 各会社詳細情報")
    lines.append("")
    lines.append("| # | 会社名 | 納付番号 | 受入科目名 | 税額合計 | チェック |")
    lines.append("|---|--------|---------|-----------|---------|---------|")
    no = 0
    for c in checklist.companies:
        for pd in c.details:
            no += 1
            lines.append(
                f"| {no} | {c.name} | {pd.payment_number} | {pd.subject_name} | "
                f"{_yen(pd.total_amount)} | □ |"
            )
    lines.append("")

    # Section 4: 突合チェック結果
    lines.append("## 突合チェック結果")
    lines.append("")
    lines.append("| 種類 | 一括納付書番号 | 受入科目 | 期待値 | 実際値 | 差額 | 結果 |")
    lines.append("|------|---------------|---------|--------|--------|------|------|")
    for v in checklist.validations:
        icon = "✓" if v.match else "✗"
        lines.append(
            f"| {v.type} | {v.bulk_payment_number or ''} | {v.subject_name or ''} | "
            f"{_yen(v.expected)} | {_yen(v.actual)} | {_yen(v.diff)} | {icon} |"
        )
    lines.append("")

    # Section 5: 納付情報(経理連絡用)
    lines.append("## 納付情報(経理連絡用)")
    lines.append("")
    lines.append("| 一括納付書番号 | 申告官署 | 受入科目 | 納付番号 | 確認番号 | 税額 | 納期限 |")
    lines.append("|---------------|---------|---------|---------|---------|------|--------|")
    for c in checklist.companies:
        for pd in c.details:
            lines.append(
                f"| {pd.bulk_payment_number} | {pd.customs_office or ''} | "
                f"{pd.subject_name} | {pd.payment_number} | "
                f"{pd.confirmation_number or ''} | {_yen(pd.total_amount)} | "
                f"{pd.deadline or ''} |"
            )
    lines.append("")

    # Section 6: 仕入書番号対応表
    invoice_blocks = []
    for c in checklist.companies:
        if not c.invoice_mappings:
            continue
        invoice_blocks.append((c.name, c.invoice_mappings))
    if invoice_blocks:
        lines.append("## 仕入書番号対応表")
        lines.append("")
        for cname, mappings in invoice_blocks:
            lines.append(f"### {cname}")
            lines.append("")
            for m in mappings:
                lines.append(
                    f"#### 一括納付書 {m.bulk_payment_number} / "
                    f"{m.customs_office or ''} / {m.subject_name}"
                )
                lines.append("")
                lines.append("| # | 本税調定日 | 輸入申告番号 | 仕入書番号下5桁 |")
                lines.append("|---|------------|--------------|-----------------|")
                for it in m.items:
                    lines.append(
                        f"| {it.no} | {it.settled_date or ''} | {it.declaration_number} | "
                        f"{_invoice_cell(it)} |"
                    )
                lines.append("")

    if checklist.warnings:
        lines.append("## 警告")
        for w in checklist.warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)


def _invoice_cell(it: InvoiceMappingItem) -> str:
    label = _INVOICE_STATUS_LABEL.get(it.status, "")
    if it.invoice_last5:
        return ", ".join(it.invoice_last5)
    return label or "(記載なし)"


# ---------------------------------------------------------------------------
# TSV (Excel paste)
# ---------------------------------------------------------------------------

def to_tsv_files(checklist: MonthlyChecklist) -> dict:
    """Return three TSV blocks keyed by section name."""
    return {
        "attached_files": _tsv_attached_files(checklist),
        "company_page_counts": _tsv_company_page_counts(checklist),
        "company_details": _tsv_company_details(checklist),
    }


def _tsv_attached_files(checklist: MonthlyChecklist) -> str:
    rows = []
    for i, uf in enumerate(checklist.uploaded_files, 1):
        rows.append("\t".join([str(i), uf.pdf_kind, uf.filename]))
    return "\n".join(rows)


def _tsv_company_page_counts(checklist: MonthlyChecklist) -> str:
    rows = []
    for i, c in enumerate(checklist.companies, 1):
        cover = c.total_cover_pages
        permit = c.total_permit_pages
        rows.append("\t".join([
            str(i),
            c.name,
            str(cover),
            str(permit),
            str(cover + permit),
            "",  # check
            "(手動確認)" if c.manual_permit_check else "",
        ]))
    return "\n".join(rows)


def _tsv_company_details(checklist: MonthlyChecklist) -> str:
    rows = []
    no = 0
    for c in checklist.companies:
        for pd in c.details:
            no += 1
            rows.append("\t".join([
                str(no),
                c.name,
                pd.payment_number,
                pd.subject_name,
                str(pd.total_amount),
                "",  # check
            ]))
    return "\n".join(rows)


def to_full_tsv(checklist: MonthlyChecklist) -> str:
    blocks = to_tsv_files(checklist)
    sections = [
        "# 添付PDFファイル名",
        blocks["attached_files"],
        "",
        "# 各会社PDF枚数確認",
        blocks["company_page_counts"],
        "",
        "# 各会社詳細情報",
        blocks["company_details"],
    ]
    return "\n".join(sections)
