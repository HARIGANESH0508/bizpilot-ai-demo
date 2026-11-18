"""Three-way matching: Purchase Order vs Supplier Invoice vs Goods Receipt Note.

Read-only: returns findings, never changes status. Mismatched invoices are never auto-approved.
"""
import difflib
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GoodsReceipt, GoodsReceiptItem, Invoice, InvoiceItem, PurchaseOrder, Supplier

SEVERITY_WEIGHT = {"low": 5, "medium": 15, "high": 30, "critical": 50}


@dataclass
class Issue:
    type: str
    severity: str
    message: str


def _key(product_code: str | None, description: str | None) -> str:
    if product_code:
        return product_code.strip().upper()
    return (description or "").strip().lower()


def risk_score(issues: list[Issue]) -> int:
    return min(100, sum(SEVERITY_WEIGHT.get(i.severity, 0) for i in issues))


def resolve_supplier(session: Session, name: str | None, tax_id: str | None) -> Supplier | None:
    if tax_id:
        found = session.scalar(select(Supplier).where(Supplier.tax_id == tax_id))
        if found:
            return found
    if not name:
        return None
    exact = session.scalar(select(Supplier).where(func.lower(Supplier.name) == name.lower()))
    if exact:
        return exact
    best, best_ratio = None, 0.85
    for supplier in session.scalars(select(Supplier)):
        ratio = difflib.SequenceMatcher(None, supplier.name.lower(), name.lower()).ratio()
        if ratio >= best_ratio:
            best, best_ratio = supplier, ratio
    return best


def _received_by_key(session: Session, po_id: int) -> dict[str, float]:
    rows = session.execute(
        select(GoodsReceiptItem.product_code, GoodsReceiptItem.description, GoodsReceiptItem.received_quantity)
        .join(GoodsReceipt, GoodsReceiptItem.grn_id == GoodsReceipt.id)
        .where(GoodsReceipt.po_id == po_id)
    ).all()
    totals: dict[str, float] = {}
    for code, desc, qty in rows:
        k = _key(code, desc)
        totals[k] = totals.get(k, 0.0) + qty
    return totals


def _previously_claimed_by_key(session: Session, po_id: int, exclude_invoice_id: int | None) -> dict[str, float]:
    query = (
        select(InvoiceItem.product_code, InvoiceItem.description, InvoiceItem.quantity)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .where(Invoice.po_id == po_id, Invoice.status != "rejected")
    )
    if exclude_invoice_id:
        query = query.where(Invoice.id != exclude_invoice_id)
    totals: dict[str, float] = {}
    for code, desc, qty in session.execute(query).all():
        k = _key(code, desc)
        totals[k] = totals.get(k, 0.0) + qty
    return totals


def match_invoice(session: Session, invoice: Invoice) -> tuple[PurchaseOrder | None, list[Issue]]:
    """Three-way match with aggregation by PO line.

    Quantities are aggregated across all invoice lines mapped to the same PO line,
    then compared against ordered, received and previously submitted non-rejected quantities.
    """
    issues: list[Issue] = []
    po_number = (invoice.po_number_extracted or "").strip()
    if not po_number:
        issues.append(Issue("missing_purchase_order", "high", "Invoice does not reference a purchase order."))
        return None, issues

    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number))
    if po is None:
        issues.append(Issue("missing_purchase_order", "high", f"Purchase order {po_number} was not found."))
        return None, issues

    if invoice.supplier_id is None:
        issues.append(Issue("invalid_supplier", "high", "Supplier is not in the approved supplier master."))
    elif invoice.supplier_id != po.supplier_id:
        issues.append(Issue("invalid_supplier", "critical",
                            f"Invoice supplier differs from the supplier on PO {po.po_number}."))

    received = _received_by_key(session, po.id)
    prior = _previously_claimed_by_key(session, po.id, invoice.id)
    has_grn = session.scalar(select(func.count(GoodsReceipt.id)).where(GoodsReceipt.po_id == po.id)) > 0
    if not has_grn:
        issues.append(Issue("missing_goods_receipt", "medium",
                            f"No goods receipt has been recorded for PO {po.po_number}."))

    # A stable key map allows all invoice rows for one PO item to be aggregated.
    po_by_key = {_key(i.product_code, i.description): i for i in po.items}
    current_qty: dict[str, float] = {}
    matched_lines: list[tuple[InvoiceItem, object, str]] = []

    for item in invoice.items:
        ref = f"Line {item.line_no} '{item.description}'"
        po_item = po_by_key.get(_key(item.product_code, item.description))
        if po_item is None:
            best_ratio, best = 0.0, None
            for candidate in po.items:
                ratio = difflib.SequenceMatcher(None, candidate.description.lower(), item.description.lower()).ratio()
                if ratio > best_ratio:
                    best_ratio, best = ratio, candidate
            if best is not None and best_ratio >= 0.85:
                po_item = best
        if po_item is None:
            issues.append(Issue("unordered_item", "high", f"{ref} is not on PO {po.po_number}."))
            continue

        key = _key(po_item.product_code, po_item.description)
        current_qty[key] = current_qty.get(key, 0.0) + float(item.quantity)
        matched_lines.append((item, po_item, key))

        if po_item.unit_price > 0:
            pct = (float(item.unit_price) - float(po_item.unit_price)) / float(po_item.unit_price) * 100
            if abs(pct) > settings.price_tolerance_pct:
                direction = "above" if pct > 0 else "below"
                severity = "high" if abs(pct) > 5 else "medium"
                issues.append(Issue("price_mismatch", severity,
                                    f"{ref}: unit price {item.unit_price:.2f} is {abs(pct):.1f}% {direction} "
                                    f"PO price {po_item.unit_price:.2f}."))

    # Check cumulative quantities once per PO line, not once per invoice row.
    for key, this_invoice_qty in current_qty.items():
        po_item = po_by_key.get(key)
        if po_item is None:
            # Fuzzy-matched PO items may not be found by their own normalized key only
            po_item = next((candidate for candidate in po.items if _key(candidate.product_code, candidate.description) == key), None)
        if po_item is None:
            continue
        prior_claimed_qty = prior.get(key, 0.0)
        cumulative_billed = prior_claimed_qty + this_invoice_qty
        if cumulative_billed > float(po_item.quantity) + 1e-9:
            issues.append(Issue("quantity_mismatch", "high",
                                f"PO item '{po_item.description}': cumulative invoiced quantity "
                                f"{cumulative_billed:g} exceeds ordered {po_item.quantity:g}."))
        if has_grn:
            received_qty = received.get(key, 0.0)
            if received_qty <= 0:
                issues.append(Issue("missing_goods_receipt", "medium",
                                    f"PO item '{po_item.description}': no goods received for this item."))
            elif cumulative_billed > received_qty + 1e-9:
                issues.append(Issue("overbilling", "high",
                                    f"PO item '{po_item.description}': cumulative billed {cumulative_billed:g} "
                                    f"exceeds received {received_qty:g} (including previously submitted "
                                    f"quantity {prior_claimed_qty:g})."))

    return po, issues
