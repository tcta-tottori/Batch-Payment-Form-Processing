"""Data models for the bulk payment checklist system.

Mirrors the entities described in section 4 of the specification.
"""
from __future__ import annotations

from typing import List, Optional, Literal

from pydantic import BaseModel, Field


class DetailItem(BaseModel):
    no: int
    settled_date: Optional[str] = None  # YYYY-MM-DD
    declaration_number: str             # 11-digit, no spaces
    amount: int


class PaymentDetail(BaseModel):
    """Single 納付番号通知 + matching 一括納付用明細書情報 entry."""

    bulk_payment_number: str
    payment_number: str                 # leading-zero preserving string
    confirmation_number: Optional[str] = None
    customs_office: Optional[str] = None  # 大阪 関西空港 等
    agent_code: Optional[str] = None
    subject_name: str                    # 関税 / 消費・地方消費税
    total_amount: int
    deadline: Optional[str] = None
    declared_total_amount: Optional[int] = None  # 一括納付用明細書の合計額
    declared_count: Optional[int] = None         # 合計件数
    items: List[DetailItem] = Field(default_factory=list)


class ImportPermit(BaseModel):
    permit_declaration_number: str
    matched_declaration_number: Optional[str] = None
    approval_date: Optional[str] = None
    invoice_numbers: List[str] = Field(default_factory=list)
    invoice_last5: List[str] = Field(default_factory=list)
    shipper_ref_nos: List[str] = Field(default_factory=list)
    total_tax: Optional[int] = None
    customs_office: Optional[str] = None
    source_pages: List[int] = Field(default_factory=list)
    source_file: Optional[str] = None


class InvoiceMappingItem(BaseModel):
    no: int
    settled_date: Optional[str] = None
    declaration_number: str
    invoice_last5: List[str] = Field(default_factory=list)
    status: Literal[
        "matched", "not_recorded", "unmatched", "not_in_invoice"
    ] = "matched"


class InvoiceMapping(BaseModel):
    bulk_payment_number: str
    customs_office: Optional[str] = None
    subject_name: str
    items: List[InvoiceMappingItem] = Field(default_factory=list)


class Company(BaseModel):
    id: str
    name: str
    agent_codes: List[str] = Field(default_factory=list)
    total_cover_pages: int = 0
    total_permit_pages: int = 0
    manual_permit_check: bool = False
    details: List[PaymentDetail] = Field(default_factory=list)
    permits: List[ImportPermit] = Field(default_factory=list)
    invoice_mappings: List[InvoiceMapping] = Field(default_factory=list)


class Validation(BaseModel):
    type: Literal[
        "detail_vs_payment",
        "declared_vs_payment",
        "permit_total_vs_bulk_total",
    ]
    bulk_payment_number: Optional[str] = None
    subject_name: Optional[str] = None
    expected: int
    actual: int
    match: bool
    diff: int = 0
    note: Optional[str] = None


class UploadedFile(BaseModel):
    filename: str
    pdf_kind: str   # 一括納付用明細書情報 / 納付番号通知情報 / 書類送付案内状 / 輸入許可通知書 / 諸掛請求書 / 延納マルチ納付書 / unknown
    pages: int


class MonthlyChecklist(BaseModel):
    month: Optional[str] = None       # 2026-03
    deadline: Optional[str] = None
    created_at: Optional[str] = None
    bulk_payment_order: List[str] = Field(default_factory=list)
    companies: List[Company] = Field(default_factory=list)
    validations: List[Validation] = Field(default_factory=list)
    uploaded_files: List[UploadedFile] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
