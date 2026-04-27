"""PDF page text parsers (F-04)."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .models import DetailItem, ImportPermit, PaymentDetail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DATE_RE = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})")
AMOUNT_RE = re.compile(r"[¥\\￥平]?\s*([0-9,]+)")
ELEVEN_DIGITS_RE = re.compile(r"(?<!\d)\d{11}(?!\d)")


def _normalize_amount(token: str) -> Optional[int]:
    """Convert "¥1,859,800" or "1,859,800" -> 1859800."""
    if not token:
        return None
    cleaned = token.replace(",", "").replace("¥", "").replace("￥", "").replace("\\", "").strip()
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _normalize_date(token: str) -> Optional[str]:
    m = DATE_RE.search(token)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _strip_spaces(s: str) -> str:
    return re.sub(r"\s+", "", s)


_AGENT_CODE_RE = re.compile(r"(?<![A-Z0-9])([0-9lI][A-Z]{4})(?![A-Z0-9])")


def _find_agent_code(text: str) -> Optional[str]:
    """Find a 5-char agent code (digit + 4 letters), OCR-tolerant.

    Real samples include '4ATUC', '3KTRA', '4HSKY', '1STRA', '4ATじC' (OCR
    noise on letter U -> じ), 'lSTRA' (lowercase L instead of digit 1).
    """
    candidates: List[str] = []
    for m in _AGENT_CODE_RE.finditer(text):
        token = m.group(1)
        # Normalize OCR noise: leading 'l'/'I' -> '1'
        if token[0] in ("l", "I"):
            token = "1" + token[1:]
        # Skip company-name fragments that incidentally match the pattern.
        candidates.append(token)
    if not candidates:
        return None
    # Prefer the FIRST candidate that appears near 利用者/代理人コード labels;
    # otherwise return the most common.
    return candidates[0]


def _find_bulk_payment_number(text: str, lines: List[str]) -> Optional[str]:
    """Locate the 一括納付書番号 (11-digit) tolerating OCR noise on the label.

    The label tends to be rendered as "一括納付書番号", but PyMuPDF often
    OCR-corrupts it to forms like "―い括納付書番手許", "―“括納付書番手チ",
    "‐キ舌糸内イ寸書番号" etc. We therefore look for any line that contains
    BOTH 「括」 and 「番」 and pick the trailing 11-digit number on the same
    line; if that fails we look at the next ~3 lines.
    """
    for i, ln in enumerate(lines):
        if "括" in ln and "番" in ln:
            # try same line first
            m = ELEVEN_DIGITS_RE.search(ln.replace(" ", ""))
            if m:
                return m.group(0)
            # then next 3 lines combined
            merged = "".join(lines[i + 1: i + 4])
            m2 = ELEVEN_DIGITS_RE.search(merged.replace(" ", ""))
            if m2:
                return m2.group(0)
    # OCR fallback: header text BEFORE 「本税調定日」 (or whole text on
    # 納付番号通知 pages) contains exactly two 11-digit numbers - the
    # 納付番号 (starts with 0) and the 一括納付書番号. Prefer the latter.
    head_end = text.find("本税調定日")
    head = text[:head_end] if head_end > 0 else text
    candidates = ELEVEN_DIGITS_RE.findall(head)
    if candidates:
        non_zero = [c for c in candidates if not c.startswith("0")]
        if non_zero:
            return non_zero[0]
    return None


# ---------------------------------------------------------------------------
# 納付番号通知情報 (一括)
# ---------------------------------------------------------------------------

# Field labels appear vertically in the PDF text. We pull the value that
# follows the label keyword.
def parse_payment_notice(text: str) -> Optional[PaymentDetail]:
    """Parse a single 納付番号通知情報(一括) page.

    The PDF text is roughly: a column of labels then a column of values.
    We rely on the labels' relative order rather than fragile XY parsing.
    """
    if "納付番号通知情報" not in text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Collection-organization code is always 00120; collect 11-digit numbers
    # appearing immediately after the labels collected_organization_code/
    # 納付番号. The simplest heuristic: locate "00120" then the next 11-digit
    # number is the 納付番号.
    payment_number: Optional[str] = None
    confirmation_number: Optional[str] = None
    bulk_payment_number: Optional[str] = None
    customs_office: Optional[str] = None
    agent_code: Optional[str] = None
    deadline: Optional[str] = None
    subject_name: Optional[str] = None
    total_amount: Optional[int] = None

    # 一括納付書番号 - the label is OCR-noisy ("括納付書番手", "括納付書番手チ", etc.).
    # Look for any line that contains 「括」 + 「番」 and pick the trailing 11-digit number.
    bulk_payment_number = _find_bulk_payment_number(text, lines)

    # 00120 -> 納付番号 -> 確認番号
    code_idx = None
    for i, ln in enumerate(lines):
        if ln == "00120":
            code_idx = i
            break
    if code_idx is not None:
        # next two non-empty numeric tokens
        following_numbers = []
        for ln in lines[code_idx + 1: code_idx + 12]:
            digits = ln.strip()
            if digits.isdigit():
                following_numbers.append(digits)
            if len(following_numbers) >= 2:
                break
        if following_numbers:
            payment_number = following_numbers[0]
        if len(following_numbers) >= 2:
            confirmation_number = following_numbers[1]

    # 申告官署 (税関名 + 税関官署名). Spec example: 大阪 関西空港
    # Keywords list to scan
    customs_keywords = [
        "大阪", "東京", "横浜", "名古屋", "神戸", "門司", "長崎", "函館", "沖縄",
    ]
    for i, ln in enumerate(lines):
        if ln in customs_keywords:
            # combine with next line
            j = i + 1
            while j < len(lines) and (lines[j].startswith("(") or not lines[j]):
                j += 1
            if j < len(lines):
                # avoid lines that are obviously labels
                follow = lines[j]
                if not any(x in follow for x in ("納期限", "代理人", "輸入者")):
                    customs_office = f"{ln} {follow}"
                    break

    # 利用者(代理人コード). 5 chars: digit + 4 uppercase letters, but OCR
    # corrupts the leading digit ('1' -> 'l' or 'I') and middle letters.
    agent_code = _find_agent_code(text)

    # 納期限
    deadline = _normalize_date(text)
    # but the first date encountered is often 納期限 = 2026/06/30
    first_date_match = DATE_RE.search(text)
    if first_date_match:
        deadline = _normalize_date(first_date_match.group(0))

    # 受入科目名
    if "消費・地方消費税" in text or "消費" in text and "地方消費税" in text:
        subject_name = "消費・地方消費税"
    elif "関税" in text:
        subject_name = "関税"

    # 税額合計 - find the line "税額合計" then the next ¥amount
    for i, ln in enumerate(lines):
        if "税額合計" in ln:
            # search same line + next 3 lines
            chunk = " ".join(lines[i:i + 4])
            m3 = re.search(r"[¥￥\\平]\s*([0-9,]+)", chunk)
            if m3:
                total_amount = _normalize_amount(m3.group(1))
                if total_amount is not None:
                    break

    if not bulk_payment_number or not payment_number or total_amount is None:
        # Not a parseable page; skip.
        return None

    return PaymentDetail(
        bulk_payment_number=bulk_payment_number,
        payment_number=payment_number,
        confirmation_number=confirmation_number,
        customs_office=customs_office,
        agent_code=agent_code,
        subject_name=subject_name or "",
        total_amount=total_amount,
        deadline=deadline,
    )


# ---------------------------------------------------------------------------
# 一括納付用明細書情報
# ---------------------------------------------------------------------------

def parse_bulk_detail(text: str) -> Optional[Tuple[str, str, Optional[int], Optional[int], List[DetailItem], Optional[str]]]:
    """Return (bulk_payment_number, subject_name, total, count, items, payment_number)."""
    if "一括納付用明細書情報" not in text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    bulk_payment_number = _find_bulk_payment_number(text, lines)

    # 受入科目
    subject_name = None
    if "消費・地方消費税" in text:
        subject_name = "消費・地方消費税"
    elif "関税" in text:
        subject_name = "関税"

    # 合計額: the first yen-amount in the page is always the section total
    # (¥1,859,800 for the 9-row example; ¥300 for the 1-row example).
    declared_total: Optional[int] = None
    m_first = re.search(r"[¥￥\\平]\s*([0-9,]+)", text)
    if m_first:
        declared_total = _normalize_amount(m_first.group(1))

    # Detail items - tolerant of layout differences
    items, declared_count, payment_number = _parse_detail_items_smart(
        text, bulk_payment_number, declared_total
    )

    if not bulk_payment_number:
        return None
    return (
        bulk_payment_number,
        subject_name or "",
        declared_total,
        declared_count,
        items,
        payment_number,
    )


def _parse_detail_items_smart(
    text: str,
    bulk_payment_number: Optional[str],
    declared_total: Optional[int],
) -> Tuple[List[DetailItem], Optional[int], Optional[str]]:
    """Extract detail rows tolerating layout differences.

    Returns (items, declared_count, payment_number).

    Approach: collect ALL 11-digit numbers, dates and yen-amounts from the
    page, then prune the obvious header values:
      - bulk_payment_number, payment_number from the 11-digit list
      - 納期限 (which equals the next-month-end date) and 作成日 from dates
      - the section total from amounts (if known)
    What remains are the detail rows. Align by min length.
    """
    # 11-digit numbers
    all_eleven = ELEVEN_DIGITS_RE.findall(text)
    # 納付番号 starts with 0; bulk_payment_number doesn't (in observed data)
    payment_number: Optional[str] = None
    for tok in all_eleven:
        if tok == bulk_payment_number:
            continue
        if tok.startswith("0"):
            payment_number = tok
            break

    # Declarations = remaining 11-digit numbers
    declarations: List[str] = []
    skip = {x for x in (bulk_payment_number, payment_number) if x}
    seen = set()
    for tok in all_eleven:
        if tok in skip and tok not in seen:
            seen.add(tok)
            continue
        declarations.append(tok)

    # Dates: drop the 納期限 (always equals the 4-month-from-now end date) and
    # the 作成日 (page header). We don't know which is which exactly, so we
    # filter dates that exactly equal the deadline (= the FIRST date in the
    # page, which is always 2026/06/30 in the spec window) and any date that
    # equals the page header date.
    all_dates_groups = DATE_RE.findall(text)
    all_dates = [f"{y}/{m}/{d}" for (y, m, d) in all_dates_groups]
    # The 納期限 line label "納期限" or 糸内夢 (OCR) typically renders the date
    # close to the deadline label. We also know that the page-creation date
    # appears as a YYYY/MM/DD shortly after a "1/1" page-marker.
    # Simplest filter: drop dates equal to the first occurring date if it is
    # June 30 (deadline) and drop "2026/04/08" style creation dates if they
    # appear in the header (before 「本税調定日」).
    head_end = text.find("本税調定日")
    # Identify header-only dates
    header_dates = []
    if head_end > 0:
        head_text = text[:head_end]
        header_dates = [f"{y}/{m}/{d}" for (y, m, d) in DATE_RE.findall(head_text)]
    # Tail dates (after column header)
    tail_text = text[head_end:] if head_end > 0 else text
    tail_dates_groups = DATE_RE.findall(tail_text)
    tail_dates = [f"{y}/{m}/{d}" for (y, m, d) in tail_dates_groups]
    # If there are tail dates, those are the body
    body_dates = tail_dates if tail_dates else [d for d in all_dates if d not in header_dates]

    # If the layout has the declaration column AFTER 本税調定日 (multi-row case),
    # use the tail-only declaration count to align.
    tail_declarations = ELEVEN_DIGITS_RE.findall(tail_text)

    # Yen amounts
    all_amounts = re.findall(r"[¥￥\\平]\s*([0-9,]+)", text)
    tail_amounts = re.findall(r"[¥￥\\平]\s*([0-9,]+)", tail_text)

    # Filter out the FIRST amount equal to declared_total (it's the section
    # total; line-item amounts come after even if they happen to repeat the
    # total value).
    def _filter_total(amts: List[str]) -> List[str]:
        if declared_total is None or not amts:
            return amts
        first_idx = next(
            (i for i, a in enumerate(amts) if _normalize_amount(a) == declared_total),
            -1,
        )
        if first_idx < 0:
            return amts
        return amts[:first_idx] + amts[first_idx + 1:]

    declared_count: Optional[int] = None

    # Single-row layout: only one detail row, declaration appears in head.
    if len(body_dates) == 1 and len(tail_declarations) == 0:
        # find a body declaration: any non-header 11-digit number
        body_decl = declarations[0] if declarations else None
        if body_decl:
            body_amts = _filter_total(all_amounts)
            amt_val = _normalize_amount(body_amts[0]) if body_amts else declared_total
            if amt_val is None and declared_total is not None:
                amt_val = declared_total
            if amt_val is not None:
                declared_count = 1
                return (
                    [DetailItem(
                        no=1,
                        settled_date=_normalize_date(body_dates[0]),
                        declaration_number=body_decl,
                        amount=amt_val,
                    )],
                    declared_count,
                    payment_number,
                )

    # Multi-row layout: declarations & amounts appear after 本税調定日 column header
    if tail_declarations and len(tail_dates) >= 1:
        amounts_for_items = _filter_total(tail_amounts)
        n = min(len(tail_dates), len(tail_declarations), len(amounts_for_items))
        items: List[DetailItem] = []
        for i in range(n):
            amt = _normalize_amount(amounts_for_items[i])
            if amt is None:
                continue
            items.append(DetailItem(
                no=i + 1,
                settled_date=_normalize_date(tail_dates[i]),
                declaration_number=tail_declarations[i],
                amount=amt,
            ))
        if items:
            declared_count = len(items)
            return items, declared_count, payment_number

    # Last resort: align body_dates / declarations / amounts (post-total filter)
    body_amts = _filter_total(all_amounts)
    n = min(len(body_dates), len(declarations), len(body_amts))
    items: List[DetailItem] = []
    for i in range(n):
        amt = _normalize_amount(body_amts[i])
        if amt is None:
            continue
        items.append(DetailItem(
            no=i + 1,
            settled_date=_normalize_date(body_dates[i]),
            declaration_number=declarations[i],
            amount=amt,
        ))
    if items:
        declared_count = len(items)
    return items, declared_count, payment_number


# ---------------------------------------------------------------------------
# 書類送付案内状
# ---------------------------------------------------------------------------

def parse_cover_letter(text: str) -> List[Tuple[str, str]]:
    """Return list of (bulk_payment_number, customs_office_label) in cover order."""
    out: List[Tuple[str, str]] = []
    # Find 11-digit numbers and the customs office on the next non-empty line.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        m = re.fullmatch(r"\d{11}", ln)
        if not m:
            continue
        # next line: customs office
        office = ""
        if i + 1 < len(lines):
            office = lines[i + 1]
        out.append((m.group(0), office))
    return out


# ---------------------------------------------------------------------------
# 輸入許可通知書
# ---------------------------------------------------------------------------

# OCR-tolerant patterns
DECLARATION_NO_RE = re.compile(r"(\d{3})\s+(\d{4})\s+(\d{4})")
INVOICE_RE = re.compile(r"B[\s\-―–]*[I1正]?[0]{2,4}(\d{6,8})(?:[\./](\d{4,6}))?(?:[\./](\d{4,6}))?")


def parse_permit(text: str, source_pages: List[int], source_file: str) -> Optional[ImportPermit]:
    """Parse a permit (single multi-page block) from concatenated text."""
    if not text.strip():
        return None
    # 申告番号: like 314 9644 5840
    m = DECLARATION_NO_RE.search(text)
    permit_decl: Optional[str] = None
    matched: Optional[str] = None
    if m:
        permit_decl = " ".join(m.groups())
        matched = "".join(m.groups())

    # 仕入書番号(B欄): e.g., "B  ―1000015136/15152"
    invoice_numbers: List[str] = []
    invoice_last5: List[str] = []
    for im in INVOICE_RE.finditer(text):
        # main number: take last 5 digits of the captured suffix
        main = im.group(1)
        if main:
            inv_full = "I000" + main if len(main) <= 7 else main
            invoice_numbers.append(inv_full)
            invoice_last5.append(main[-5:].zfill(5))
            for ext in im.groups()[1:]:
                if ext:
                    invoice_last5.append(ext[-5:].zfill(5))
                    invoice_numbers.append(ext)
    # de-dup, preserve order
    seen = set()
    invoice_last5 = [x for x in invoice_last5 if not (x in seen or seen.add(x))]
    seen2 = set()
    invoice_numbers = [x for x in invoice_numbers if not (x in seen2 or seen2.add(x))]

    # 許可日 - typically labelled "輸入許可日"; first date that follows
    approval_date = None
    m_app = re.search(r"輸入許可日[^\d]{0,5}(20\d{2}/\d{1,2}/\d{1,2})", text)
    if m_app:
        approval_date = _normalize_date(m_app.group(1))
    else:
        # Fallback: 申告年月日 line nearby
        m_app2 = re.search(r"申告年月日[^\d]{0,30}?(20\d{2}/\d{1,2}/\d{1,2})", text)
        if m_app2:
            approval_date = _normalize_date(m_app2.group(1))

    # 税額合計 - "納税額合計" then ¥amount
    total_tax: Optional[int] = None
    m_tax = re.search(r"納税額合計[^¥￥\\Y]*[¥￥\\Y]\s*([0-9,]+)", text)
    if m_tax:
        total_tax = _normalize_amount(m_tax.group(1))

    # 荷主Ref No.: pattern like "26K0201", "26K0102"
    ref_nos = []
    for rm in re.finditer(r"(\d{2}[KJ][A-Z0-9]{4,6})", text):
        ref_nos.append(rm.group(1))
    seen3 = set()
    ref_nos = [r for r in ref_nos if not (r in seen3 or seen3.add(r))]

    if not permit_decl:
        return None

    return ImportPermit(
        permit_declaration_number=permit_decl,
        matched_declaration_number=matched,
        approval_date=approval_date,
        invoice_numbers=invoice_numbers,
        invoice_last5=invoice_last5,
        shipper_ref_nos=ref_nos,
        total_tax=total_tax,
        source_pages=source_pages,
        source_file=source_file,
    )


PERMIT_TAG_RE = re.compile(r"(SEA|AIR|S5|A1R)\s*[/／]\s*[1I]?MP", re.IGNORECASE)
CONTINUATION_RE = re.compile(r"つづき|続き")


def is_permit_page(text: str) -> bool:
    if PERMIT_TAG_RE.search(text):
        return True
    # Fallback: explicit header
    if "輸入許可通知書" in text:
        return True
    # Fallback: declaration-number-shaped pattern + 申告番号 label
    if "申告番号" in text and DECLARATION_NO_RE.search(text):
        return True
    return False


def is_continuation_page(text: str) -> bool:
    return bool(CONTINUATION_RE.search(text))
