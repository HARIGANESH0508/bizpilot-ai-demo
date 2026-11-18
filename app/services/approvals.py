"""Human approval workflow. No state here ever triggers a payment; approval only records authorization."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ApprovalRequest, Discrepancy, Invoice, Setting, utcnow
from app.security import can
from app.services import audit
from app.services.errors import PermissionDenied, WorkflowError
from app.services.matching import Issue

BLOCKING_SEVERITIES = {"high", "critical"}
TERMINAL_STATUSES = {"approved", "rejected"}


def get_threshold(session: Session) -> float:
    row = session.get(Setting, "approval_threshold")
    return float(row.value) if row else settings.approval_threshold


def set_threshold(session: Session, actor: str, role: str, value: float) -> float:
    if not can(role, "admin"):
        raise PermissionDenied("Only administrators can change approval thresholds.")
    row = session.get(Setting, "approval_threshold")
    old = get_threshold(session)
    if row:
        row.value = str(value)
    else:
        session.add(Setting(key="approval_threshold", value=str(value)))
    audit.record(session, actor, "threshold_changed", "setting", "approval_threshold", {"old": old, "new": value})
    session.commit()
    return value


def open_blocking_discrepancies(invoice: Invoice) -> list[Discrepancy]:
    return [d for d in invoice.discrepancies if not d.resolved and d.severity in BLOCKING_SEVERITIES]


def route(session: Session, invoice: Invoice, issues: list[Issue], duplicate_types: set[str]) -> str:
    """Stores discrepancies and decides the next status. Never returns 'approved'."""
    resolved_by_key = {(d.type, d.message): d for d in invoice.discrepancies if d.resolved}
    for old in list(invoice.discrepancies):
        if not old.resolved:
            session.delete(old)
    session.flush()

    for issue in issues:
        prior = resolved_by_key.get((issue.type, issue.message))
        session.add(Discrepancy(
            invoice_id=invoice.id, type=issue.type, severity=issue.severity, message=issue.message,
            resolved=prior is not None,
            resolution_note=prior.resolution_note if prior else None,
            resolved_by=prior.resolved_by if prior else None,
            resolved_at=prior.resolved_at if prior else None,
        ))
    session.flush()
    session.refresh(invoice)

    if invoice.missing_fields and invoice.missing_fields != "[]":
        return "needs_information"
    if open_blocking_discrepancies(invoice):
        return "flagged"
    return "awaiting_approval"


def ensure_open_request(session: Session, invoice: Invoice, requested_by: str) -> ApprovalRequest:
    existing = session.scalar(select(ApprovalRequest).where(
        ApprovalRequest.invoice_id == invoice.id, ApprovalRequest.status == "open"))
    if existing:
        return existing
    req = ApprovalRequest(invoice_id=invoice.id, requested_by=requested_by, status="open")
    session.add(req)
    session.flush()
    return req


def _open_request(session: Session, invoice_id: int) -> ApprovalRequest | None:
    return session.scalar(select(ApprovalRequest).where(
        ApprovalRequest.invoice_id == invoice_id, ApprovalRequest.status == "open"))


def approve(session: Session, invoice: Invoice, actor: str, role: str, notes: str = "") -> Invoice:
    if not can(role, "approve"):
        raise PermissionDenied("Your role cannot approve invoices.")
    if invoice.status not in {"awaiting_approval", "flagged"}:
        raise WorkflowError(f"Invoice cannot be approved from status '{invoice.status}'.")
    if invoice.missing_fields and invoice.missing_fields != "[]":
        raise WorkflowError("Invoice has missing required fields; request information first.")
    blocking = open_blocking_discrepancies(invoice)
    if blocking:
        types = ", ".join(sorted({d.type for d in blocking}))
        raise WorkflowError(f"Resolve high or critical discrepancies before approval: {types}.")
    if invoice.grand_total > get_threshold(session) and not can(role, "approve_above_threshold"):
        raise PermissionDenied("This invoice is above the approval threshold and needs an administrator.")

    invoice.status = "approved"
    req = _open_request(session, invoice.id)
    if req:
        req.status, req.decision, req.decided_by = "closed", "approved", actor
        req.decided_at, req.reviewer_notes = utcnow(), notes
    audit.record(session, actor, "invoice_approved", "invoice", invoice.id,
                 {"grand_total": invoice.grand_total, "notes": notes})
    session.commit()
    return invoice


def reject(session: Session, invoice: Invoice, actor: str, role: str, notes: str = "") -> Invoice:
    if not can(role, "reject"):
        raise PermissionDenied("Your role cannot reject invoices.")
    if invoice.status in TERMINAL_STATUSES:
        raise WorkflowError(f"Invoice is already {invoice.status}.")
    invoice.status = "rejected"
    req = _open_request(session, invoice.id)
    if req:
        req.status, req.decision, req.decided_by = "closed", "rejected", actor
        req.decided_at, req.reviewer_notes = utcnow(), notes
    audit.record(session, actor, "invoice_rejected", "invoice", invoice.id, {"notes": notes})
    session.commit()
    return invoice


def request_information(session: Session, invoice: Invoice, actor: str, role: str, notes: str = "") -> Invoice:
    if not can(role, "review"):
        raise PermissionDenied("Your role cannot request information.")
    if invoice.status in TERMINAL_STATUSES:
        raise WorkflowError(f"Invoice is already {invoice.status}.")
    invoice.status = "needs_information"
    req = _open_request(session, invoice.id)
    if req:
        req.decision, req.decided_by, req.decided_at, req.reviewer_notes = "info_requested", actor, utcnow(), notes
    audit.record(session, actor, "information_requested", "invoice", invoice.id, {"notes": notes})
    session.commit()
    return invoice


def resolve_discrepancy(session: Session, discrepancy: Discrepancy, actor: str, role: str, note: str) -> Discrepancy:
    if not can(role, "resolve_discrepancy"):
        raise PermissionDenied("Your role cannot resolve discrepancies.")
    if discrepancy.resolved:
        raise WorkflowError("Discrepancy is already resolved.")
    discrepancy.resolved = True
    discrepancy.resolution_note = note
    discrepancy.resolved_by = actor
    discrepancy.resolved_at = utcnow()
    audit.record(session, actor, "discrepancy_resolved", "discrepancy", discrepancy.id,
                 {"invoice_id": discrepancy.invoice_id, "type": discrepancy.type, "note": note})
    session.commit()
    return discrepancy
