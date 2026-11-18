"""Business tools the agent may call. Each reads or writes the real database. The LLM never supplies results.

Write tools (create_approval_request, approve_invoice, reject_invoice) are routed through the agent's
confirmation step and enforce the same role checks as the API.
"""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Invoice, PurchaseOrder, Supplier
from app.services import analytics, approvals, audit, invoices, matching
from app.services.duplicates import find_duplicates
from app.services.errors import NotFound
from app.services import validation as val

WRITE_TOOLS = {"create_approval_request", "approve_invoice", "reject_invoice"}


def _inv(session: Session, invoice_id: int) -> Invoice:
    return invoices.get_invoice(session, int(invoice_id))


def _brief(inv: Invoice) -> dict:
    return {"id": inv.id, "invoice_number": inv.invoice_number, "supplier": inv.supplier_name,
            "grand_total": inv.grand_total, "currency": inv.currency, "status": inv.status,
            "risk_score": inv.risk_score}


def get_invoice_summary(session: Session, **_) -> dict:
    return analytics.summary(session)


def search_invoice(session: Session, query: str, **_) -> list[dict]:
    q = f"%{query.strip()}%"
    rows = session.scalars(select(Invoice).where(or_(
        Invoice.invoice_number.ilike(q), Invoice.supplier_name.ilike(q),
        Invoice.original_filename.ilike(q))).limit(20)).all()
    return [_brief(i) for i in rows]


def get_pending_invoices(session: Session, **_) -> list[dict]:
    rows = session.scalars(select(Invoice).where(Invoice.status.in_(
        ["pending_review", "awaiting_approval", "needs_information"]))).all()
    return [_brief(i) for i in rows]


def get_flagged_invoices(session: Session, **_) -> list[dict]:
    rows = session.scalars(select(Invoice).where(Invoice.status == "flagged")).all()
    out = []
    for inv in rows:
        data = _brief(inv)
        data["issues"] = [d.type for d in inv.discrepancies if not d.resolved]
        out.append(data)
    return out


def get_approved_invoices(session: Session, **_) -> list[dict]:
    return [_brief(i) for i in session.scalars(select(Invoice).where(Invoice.status == "approved")).all()]


def extract_invoice_data(session: Session, invoice_id: int, **_) -> dict:
    inv = _inv(session, invoice_id)
    return {"invoice": invoices.serialize_invoice(inv, detail=True)}


def validate_invoice(session: Session, invoice_id: int, **_) -> dict:
    """Read-only: recomputes totals and checks consistency without saving."""
    inv = _inv(session, invoice_id)
    extracted = invoices.invoice_to_extracted(inv)
    issues = val.validate_invoice(extracted)
    totals = val.compute_totals(extracted.items, extracted.discount_total, extracted.vat_rate) if extracted.items else None
    return {"invoice_id": inv.id, "recomputed_totals": totals,
            "issues": [i.model_dump() for i in issues], "valid": not issues}


def match_purchase_order(session: Session, invoice_id: int, **_) -> dict:
    inv = _inv(session, invoice_id)
    po, issues = matching.match_invoice(session, inv)
    return {"invoice_id": inv.id, "purchase_order": po.po_number if po else None,
            "issues": [i.__dict__ for i in issues], "risk_score": matching.risk_score(issues)}


def detect_duplicate_invoice(session: Session, invoice_id: int, **_) -> dict:
    inv = _inv(session, invoice_id)
    findings = find_duplicates(session, sha256=inv.file_sha256, supplier_id=inv.supplier_id,
                               supplier_name=inv.supplier_name, invoice_number=inv.invoice_number,
                               grand_total=inv.grand_total, invoice_date=inv.invoice_date,
                               exclude_invoice_id=inv.id)
    return {"invoice_id": inv.id, "is_duplicate": any(f.confidence in {"exact", "high"} for f in findings),
            "findings": [f.__dict__ for f in findings]}


def compare_invoice_line_items(session: Session, invoice_id: int, **_) -> dict:
    inv = _inv(session, invoice_id)
    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.id == inv.po_id)) if inv.po_id else None
    po_lines = {(i.product_code or i.description).upper(): i for i in po.items} if po else {}
    rows = []
    for item in inv.items:
        key = (item.product_code or item.description).upper()
        ref = po_lines.get(key)
        rows.append({
            "line_no": item.line_no, "description": item.description,
            "invoiced_qty": item.quantity, "invoiced_unit_price": item.unit_price,
            "po_qty": ref.quantity if ref else None, "po_unit_price": ref.unit_price if ref else None,
            "matched_to_po": ref is not None,
        })
    return {"invoice_id": inv.id, "purchase_order": po.po_number if po else None, "lines": rows}


def get_supplier_details(session: Session, query: str, **_) -> dict:
    q = query.strip()
    supplier = session.get(Supplier, int(q)) if q.isdigit() else None
    if supplier is None:
        supplier = session.scalar(select(Supplier).where(Supplier.name.ilike(f"%{q}%")))
    if supplier is None:
        raise NotFound(f"No supplier matches '{query}'.")
    count, value = session.execute(select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.grand_total), 0))
                                   .where(Invoice.supplier_id == supplier.id)).one()
    return {"id": supplier.id, "name": supplier.name, "tax_id": supplier.tax_id, "address": supplier.address,
            "invoices": count, "invoiced_value": round(float(value), 2)}


def get_purchase_order_details(session: Session, po_number: str, **_) -> dict:
    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number.strip()))
    if po is None:
        raise NotFound(f"Purchase order {po_number} was not found.")
    return {"po_number": po.po_number, "supplier": po.supplier.name, "status": po.status,
            "currency": po.currency,
            "items": [{"code": i.product_code, "description": i.description, "quantity": i.quantity,
                       "unit_price": i.unit_price} for i in po.items]}


def create_approval_request(session: Session, invoice_id: int, actor: str, role: str, **_) -> dict:
    from app.security import can
    from app.services.errors import PermissionDenied
    if not can(role, "review"):
        raise PermissionDenied("Your role cannot create approval requests.")
    inv = _inv(session, invoice_id)
    req = approvals.ensure_open_request(session, inv, requested_by=actor)
    audit.record(session, actor, "approval_requested", "invoice", inv.id, {"request_id": req.id})
    session.commit()
    return {"invoice_id": inv.id, "approval_request_id": req.id, "status": inv.status}


def approve_invoice(session: Session, invoice_id: int, actor: str, role: str, notes: str = "", **_) -> dict:
    inv = approvals.approve(session, _inv(session, invoice_id), actor, role, notes)
    return {"invoice_id": inv.id, "status": inv.status}


def reject_invoice(session: Session, invoice_id: int, actor: str, role: str, notes: str = "", **_) -> dict:
    inv = approvals.reject(session, _inv(session, invoice_id), actor, role, notes)
    return {"invoice_id": inv.id, "status": inv.status}


def get_audit_history(session: Session, invoice_id: int | None = None, limit: int = 20, **_) -> list[dict]:
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if invoice_id is not None:
        query = query.where(AuditLog.entity_type == "invoice", AuditLog.entity_id == str(invoice_id))
    return [{"at": r.created_at.isoformat(), "actor": r.actor, "action": r.action,
             "entity": f"{r.entity_type}:{r.entity_id}", "details": r.details}
            for r in session.scalars(query).all()]


def generate_financial_report(session: Session, **_) -> dict:
    return analytics.full_report(session)


REGISTRY = {
    "get_invoice_summary": get_invoice_summary,
    "search_invoice": search_invoice,
    "get_pending_invoices": get_pending_invoices,
    "get_flagged_invoices": get_flagged_invoices,
    "get_approved_invoices": get_approved_invoices,
    "extract_invoice_data": extract_invoice_data,
    "validate_invoice": validate_invoice,
    "match_purchase_order": match_purchase_order,
    "detect_duplicate_invoice": detect_duplicate_invoice,
    "compare_invoice_line_items": compare_invoice_line_items,
    "get_supplier_details": get_supplier_details,
    "get_purchase_order_details": get_purchase_order_details,
    "create_approval_request": create_approval_request,
    "approve_invoice": approve_invoice,
    "reject_invoice": reject_invoice,
    "get_audit_history": get_audit_history,
    "generate_financial_report": generate_financial_report,
}
