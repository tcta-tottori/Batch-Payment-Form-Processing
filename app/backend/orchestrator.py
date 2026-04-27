"""High-level pipeline that turns classified PDFs into a MonthlyChecklist."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

import fitz

from .classifier import (
    ClassifiedDocument,
    PDF_KIND_BULK_BUNDLE,
    PDF_KIND_BULK_DETAIL,
    PDF_KIND_COVER_LETTER,
    PDF_KIND_DEFERRED_MULTI,
    PDF_KIND_FREIGHT_INVOICE,
    PDF_KIND_PAYMENT_NOTICE,
    PDF_KIND_PERMIT,
)
from .models import (
    Company,
    DetailItem,
    ImportPermit,
    InvoiceMapping,
    InvoiceMappingItem,
    MonthlyChecklist,
    PaymentDetail,
    UploadedFile,
    Validation,
)
from .parsers import (
    is_continuation_page,
    is_permit_page,
    parse_bulk_detail,
    parse_cover_letter,
    parse_payment_notice,
    parse_permit,
)


# Mapping of agent code -> (company id, display name)
AGENT_TO_COMPANY: Dict[str, Tuple[str, str]] = {
    "3KTRA": ("tradia", "トレーディア株式会社"),
    "2YTRA": ("tradia", "トレーディア株式会社"),
    "1STRA": ("tradia", "トレーディア株式会社"),
    "4HSKY": ("shinkoyo", "新港洋海運株式会社"),
    "4ATUC": ("yusen_logistics", "郵船ロジスティクス株式会社"),
    "3HKAM": ("kamigumi", "株式会社上組"),
}


def _company_for_agent(agent_code: Optional[str]) -> Tuple[str, str]:
    """Return (id, name) for an agent code; falls back to the code itself."""
    if not agent_code:
        return ("unknown", "不明")
    return AGENT_TO_COMPANY.get(agent_code, (agent_code.lower(), f"通関業者 ({agent_code})"))


def process_classified_documents(docs: List[ClassifiedDocument]) -> MonthlyChecklist:
    """Build a MonthlyChecklist from already-classified documents."""

    payment_notices: Dict[Tuple[str, str], PaymentDetail] = {}
    bulk_details: Dict[Tuple[str, str], dict] = {}
    bulk_payment_order: List[str] = []
    cover_pages_by_kind: Dict[str, int] = defaultdict(int)
    permits: List[ImportPermit] = []
    uploaded_files: List[UploadedFile] = []
    warnings: List[str] = []
    deadline: Optional[str] = None

    for doc in docs:
        uploaded_files.append(UploadedFile(
            filename=doc.filename,
            pdf_kind=doc.overall_kind,
            pages=doc.page_count,
        ))

        # Reopen file for content access
        if doc.overall_kind in (
            PDF_KIND_BULK_BUNDLE,
            PDF_KIND_BULK_DETAIL,
            PDF_KIND_PAYMENT_NOTICE,
            PDF_KIND_COVER_LETTER,
        ):
            for page in doc.pages:
                text = page.text
                if page.kind == PDF_KIND_COVER_LETTER:
                    pairs = parse_cover_letter(text)
                    for bpn, _ in pairs:
                        if bpn not in bulk_payment_order:
                            bulk_payment_order.append(bpn)
                elif page.kind == PDF_KIND_PAYMENT_NOTICE:
                    pn = parse_payment_notice(text)
                    if pn:
                        key = (pn.bulk_payment_number, pn.subject_name)
                        payment_notices[key] = pn
                        if pn.bulk_payment_number not in bulk_payment_order:
                            bulk_payment_order.append(pn.bulk_payment_number)
                        if pn.deadline:
                            deadline = pn.deadline
                elif page.kind == PDF_KIND_BULK_DETAIL:
                    bd = parse_bulk_detail(text)
                    if bd:
                        bpn, subject, dec_total, dec_count, items, payment_no = bd
                        bulk_details[(bpn, subject)] = {
                            "items": items,
                            "declared_total": dec_total,
                            "declared_count": dec_count,
                            "payment_number": payment_no,
                        }
                        if bpn not in bulk_payment_order:
                            bulk_payment_order.append(bpn)
                # cover-pages count for the company-level tally is computed
                # later when we have agent_code per page.

        if doc.overall_kind in (PDF_KIND_PERMIT, PDF_KIND_FREIGHT_INVOICE):
            permits.extend(_extract_permits_from_doc(doc))

    # Merge payment notices and bulk details into PaymentDetail objects
    merged: Dict[Tuple[str, str], PaymentDetail] = {}
    for key, pn in payment_notices.items():
        bd = bulk_details.get(key)
        if bd:
            pn.items = bd["items"]
            pn.declared_total_amount = bd["declared_total"]
            pn.declared_count = bd["declared_count"]
        merged[key] = pn

    # Propagate agent_code across payment notices that share a bulk number.
    bpn_to_agent: Dict[str, str] = {}
    for pn in merged.values():
        if pn.agent_code and pn.bulk_payment_number not in bpn_to_agent:
            bpn_to_agent[pn.bulk_payment_number] = pn.agent_code
    for pn in merged.values():
        if not pn.agent_code and pn.bulk_payment_number in bpn_to_agent:
            pn.agent_code = bpn_to_agent[pn.bulk_payment_number]

    # Some bulk_details may exist without a matching payment_notice; preserve
    # them so the user is alerted.
    for key, bd in bulk_details.items():
        if key in merged:
            continue
        bpn, subject = key
        merged[key] = PaymentDetail(
            bulk_payment_number=bpn,
            payment_number=bd.get("payment_number") or "",
            subject_name=subject,
            total_amount=bd.get("declared_total") or 0,
            items=bd.get("items", []),
            declared_total_amount=bd.get("declared_total"),
            declared_count=bd.get("declared_count"),
        )
        warnings.append(f"納付番号通知が見つからない一括納付明細書情報があります: {bpn} ({subject})")

    # ------------------------------------------------------------------
    # Group by company (agent code -> company id)
    # ------------------------------------------------------------------
    companies_dict: Dict[str, Company] = {}
    for key, pd in merged.items():
        cid, cname = _company_for_agent(pd.agent_code)
        company = companies_dict.get(cid)
        if not company:
            company = Company(id=cid, name=cname)
            companies_dict[cid] = company
        if pd.agent_code and pd.agent_code not in company.agent_codes:
            company.agent_codes.append(pd.agent_code)
        company.details.append(pd)

    # Bind permits to companies. Match each permit to whatever company has
    # a detail item whose declaration_number equals it.
    decl_to_company: Dict[str, str] = {}
    for company in companies_dict.values():
        for pd in company.details:
            for it in pd.items:
                decl_to_company[it.declaration_number] = company.id

    for permit in permits:
        cid = None
        if permit.matched_declaration_number:
            cid = decl_to_company.get(permit.matched_declaration_number)
        if not cid:
            # Fall back to the first 'tradia' company if we have one (since
            # most permits originate from 諸掛請求書 from トレーディア).
            cid = next(
                (c.id for c in companies_dict.values() if c.id == "tradia"),
                None,
            )
        if cid and cid in companies_dict:
            companies_dict[cid].permits.append(permit)

    # cover/permit page tallies
    cover_pages_per_company: Dict[str, int] = defaultdict(int)
    for doc in docs:
        if doc.overall_kind not in (
            PDF_KIND_BULK_BUNDLE,
            PDF_KIND_BULK_DETAIL,
            PDF_KIND_PAYMENT_NOTICE,
            PDF_KIND_COVER_LETTER,
        ):
            continue
        # For each page, find its agent code (if recognized) and credit the
        # company. Cover-letter pages are credited to the trailing detail's
        # company (best-effort: spread across all companies in the bundle).
        for page in doc.pages:
            if page.kind in (PDF_KIND_PAYMENT_NOTICE, PDF_KIND_BULK_DETAIL):
                # Use parsers to find the agent code via cached parses
                if page.kind == PDF_KIND_PAYMENT_NOTICE:
                    pn = parse_payment_notice(page.text)
                    code = pn.agent_code if pn else None
                else:
                    bd = parse_bulk_detail(page.text)
                    # bulk_detail doesn't expose agent code yet; reuse PN's
                    bpn = bd[0] if bd else None
                    subject = bd[1] if bd else None
                    pn = payment_notices.get((bpn, subject)) if bpn else None
                    code = pn.agent_code if pn else None
                cid, _ = _company_for_agent(code)
                cover_pages_per_company[cid] += 1
            elif page.kind == PDF_KIND_COVER_LETTER:
                # Credit the cover letter to the company that owns the most
                # pages on the same document.
                pass

    for cid, count in cover_pages_per_company.items():
        if cid in companies_dict:
            companies_dict[cid].total_cover_pages = count

    # Permits page count
    for company in companies_dict.values():
        company.total_permit_pages = sum(len(p.source_pages) for p in company.permits)
        # Mark "manual permit check" for companies that have no permits but
        # are configured as manual-check (郵船 / 上組 per spec).
        if company.id in ("yusen_logistics", "kamigumi") and not company.permits:
            company.manual_permit_check = True

    # ------------------------------------------------------------------
    # Validation (F-05)
    # ------------------------------------------------------------------
    validations: List[Validation] = []
    for key, pd in merged.items():
        # detail items vs payment notice total
        line_sum = sum(it.amount for it in pd.items)
        validations.append(Validation(
            type="detail_vs_payment",
            bulk_payment_number=pd.bulk_payment_number,
            subject_name=pd.subject_name,
            expected=line_sum,
            actual=pd.total_amount,
            match=line_sum == pd.total_amount,
            diff=pd.total_amount - line_sum,
            note="明細合計と納付番号通知の税額合計の突合",
        ))
        # bulk-detail declared total vs payment notice total
        if pd.declared_total_amount is not None:
            validations.append(Validation(
                type="declared_vs_payment",
                bulk_payment_number=pd.bulk_payment_number,
                subject_name=pd.subject_name,
                expected=pd.declared_total_amount,
                actual=pd.total_amount,
                match=pd.declared_total_amount == pd.total_amount,
                diff=pd.total_amount - pd.declared_total_amount,
                note="一括納付用明細書情報の合計額と納付番号通知の税額合計の突合",
            ))

    # ------------------------------------------------------------------
    # Invoice number mappings (F-10)
    # ------------------------------------------------------------------
    permits_by_decl: Dict[str, ImportPermit] = {
        p.matched_declaration_number: p
        for p in permits
        if p.matched_declaration_number
    }
    for company in companies_dict.values():
        # Only build invoice mappings if this company has any permits.
        if not company.permits:
            continue
        for pd in company.details:
            mapping = InvoiceMapping(
                bulk_payment_number=pd.bulk_payment_number,
                customs_office=pd.customs_office,
                subject_name=pd.subject_name,
            )
            for it in pd.items:
                permit = permits_by_decl.get(it.declaration_number)
                if permit and permit.invoice_last5:
                    mapping.items.append(InvoiceMappingItem(
                        no=it.no,
                        settled_date=it.settled_date,
                        declaration_number=it.declaration_number,
                        invoice_last5=permit.invoice_last5,
                        status="matched",
                    ))
                elif permit:
                    mapping.items.append(InvoiceMappingItem(
                        no=it.no,
                        settled_date=it.settled_date,
                        declaration_number=it.declaration_number,
                        invoice_last5=[],
                        status="not_recorded",
                    ))
                else:
                    # No permit found for this declaration. Most likely it is
                    # a month-end (delayed) declaration.
                    mapping.items.append(InvoiceMappingItem(
                        no=it.no,
                        settled_date=it.settled_date,
                        declaration_number=it.declaration_number,
                        invoice_last5=[],
                        status="not_in_invoice",
                    ))
            if mapping.items:
                company.invoice_mappings.append(mapping)

    # Sort companies in cover-letter order if available; otherwise alphabetical
    company_order = list(companies_dict.values())
    company_order.sort(key=lambda c: c.name)

    today = date.today().isoformat()

    # Determine the data month from the earliest detail settled_date
    months = {
        it.settled_date[:7]
        for c in company_order
        for pd in c.details
        for it in pd.items
        if it.settled_date
    }
    month_str: Optional[str] = sorted(months)[-1] if months else None

    return MonthlyChecklist(
        month=month_str,
        deadline=deadline,
        created_at=today,
        bulk_payment_order=bulk_payment_order,
        companies=company_order,
        validations=validations,
        uploaded_files=uploaded_files,
        warnings=warnings,
    )


def _extract_permits_from_doc(doc: ClassifiedDocument) -> List[ImportPermit]:
    """Group permit pages and parse each group into an ImportPermit."""
    pages = doc.pages
    groups: List[List[int]] = []
    current: Optional[List[int]] = None
    for page in pages:
        text = page.text
        if is_permit_page(text):
            if not is_continuation_page(text):
                if current:
                    groups.append(current)
                current = [page.page_index]
            else:
                if current is None:
                    current = [page.page_index]
                else:
                    current.append(page.page_index)
        else:
            if current:
                groups.append(current)
                current = None
    if current:
        groups.append(current)

    # Concatenate text for each group, parse
    fitz_doc = fitz.open(doc.file_path)
    permits: List[ImportPermit] = []
    try:
        for group in groups:
            text = "\n".join(fitz_doc[i].get_text() or "" for i in group)
            permit = parse_permit(text, [i + 1 for i in group], doc.filename)
            if permit:
                permits.append(permit)
    finally:
        fitz_doc.close()
    return permits


# ---------------------------------------------------------------------------
# Permit PDF extraction & sorting (F-08, F-09)
# ---------------------------------------------------------------------------

def build_sorted_permit_pdf(
    docs: List[ClassifiedDocument],
    checklist: MonthlyChecklist,
    output_path: str,
) -> Tuple[int, int]:
    """Build a re-ordered permit-only PDF.

    Returns (permit_count, total_pages).
    """
    # Collect all permits with their source pages, indexed by source file.
    source_docs = [
        d for d in docs
        if d.overall_kind in (PDF_KIND_PERMIT, PDF_KIND_FREIGHT_INVOICE)
    ]

    # Re-extract permits from sources to maintain page references
    permits_with_source: List[Tuple[ImportPermit, str]] = []
    for d in source_docs:
        for p in _extract_permits_from_doc(d):
            permits_with_source.append((p, d.file_path))

    # Sort by cover-letter order
    decl_order: List[str] = []
    for bpn in checklist.bulk_payment_order:
        for company in checklist.companies:
            for pd in company.details:
                if pd.bulk_payment_number != bpn:
                    continue
                # second key: 関税 -> 消費・地方消費税
                # third key: settled_date asc; fourth key: decl_number asc
                ordered = sorted(
                    pd.items,
                    key=lambda it: (it.settled_date or "", it.declaration_number),
                )
                for it in ordered:
                    if it.declaration_number not in decl_order:
                        decl_order.append(it.declaration_number)

    permit_lookup: Dict[str, Tuple[ImportPermit, str]] = {}
    for permit, src in permits_with_source:
        if permit.matched_declaration_number:
            permit_lookup.setdefault(permit.matched_declaration_number, (permit, src))

    out = fitz.open()
    total_pages = 0
    permit_count = 0
    open_sources: Dict[str, fitz.Document] = {}
    try:
        for decl in decl_order:
            entry = permit_lookup.get(decl)
            if not entry:
                continue
            permit, src = entry
            src_doc = open_sources.get(src)
            if src_doc is None:
                src_doc = fitz.open(src)
                open_sources[src] = src_doc
            for one_indexed in permit.source_pages:
                page_idx = one_indexed - 1
                if 0 <= page_idx < len(src_doc):
                    out.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)
                    total_pages += 1
            permit_count += 1
        out.save(output_path)
    finally:
        out.close()
        for d in open_sources.values():
            d.close()
    return permit_count, total_pages
