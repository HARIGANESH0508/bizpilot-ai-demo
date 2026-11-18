"""Layered duplicate detection. Exact file duplicates are blocked; everything else is flagged for a human.

Levels:
  1. file_hash            exact SHA-256 match              -> blocked (not stored twice)
  2. supplier_invoice_no  same supplier + normalized number -> high, flagged for review
  3. fuzzy_invoice_no     same supplier, similar number and same total -> possible, flagged
  4. amount_date          same supplier, same total, dates within 3 days -> possible, flagged
"""
import difflib
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invoice


@dataclass
class DuplicateFinding:
    kind: str
    confidence: str  # exact | high | possible
    reason: str
    invoice_id: int | None = None


def normalize_invoice_number(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def find_duplicates(
    session: Session,
    *,
    sha256: str | None,
    supplier_id: int | None,
    supplier_name: str | None,
    invoice_number: str | None,
    grand_total: float,
    invoice_date,
    exclude_invoice_id: int | None = None,
) -> list[DuplicateFinding]:
    findings: list[DuplicateFinding] = []

    if sha256:
        query = select(Invoice).where(Invoice.file_sha256 == sha256)
        if exclude_invoice_id:
            query = query.where(Invoice.id != exclude_invoice_id)
        exact = session.scalar(query)
        if exact:
            return [DuplicateFinding("file_hash", "exact",
                                     f"Identical file already uploaded as invoice #{exact.id}.", exact.id)]

    if supplier_id is not None:
        same_supplier = Invoice.supplier_id == supplier_id
    elif supplier_name:
        same_supplier = Invoice.supplier_name == supplier_name
    else:
        return findings

    query = select(Invoice).where(same_supplier).limit(500)
    if exclude_invoice_id:
        query = query.where(Invoice.id != exclude_invoice_id)
    candidates = session.scalars(query).all()

    norm = normalize_invoice_number(invoice_number)
    for cand in candidates:
        cand_norm = normalize_invoice_number(cand.invoice_number)
        same_total = abs(cand.grand_total - grand_total) < 0.01
        if norm and cand_norm == norm:
            findings.append(DuplicateFinding(
                "supplier_invoice_number", "high",
                f"This supplier already invoiced number {cand.invoice_number} (invoice #{cand.id}).", cand.id))
        elif norm and cand_norm and same_total and difflib.SequenceMatcher(None, norm, cand_norm).ratio() >= 0.9:
            findings.append(DuplicateFinding(
                "fuzzy_invoice_number", "possible",
                f"Invoice number {cand.invoice_number} is very similar and the total matches (invoice #{cand.id}).",
                cand.id))
        elif same_total and invoice_date and cand.invoice_date and \
                abs(cand.invoice_date - invoice_date) <= timedelta(days=3):
            findings.append(DuplicateFinding(
                "amount_date_similarity", "possible",
                f"Same supplier, same total and invoice date within 3 days of invoice #{cand.id}.", cand.id))
    return findings
