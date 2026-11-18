import pytest

from app.services import approvals
from app.services.errors import PermissionDenied, WorkflowError
from app.models import ApprovalRequest, AuditLog
from tests.conftest import by_file, discrepancy_types


def test_viewer_cannot_approve(seeded):
    inv = by_file(seeded, "INV-2026-001_clean")
    with pytest.raises(PermissionDenied):
        approvals.approve(seeded, inv, "viewer", "viewer")
    assert inv.status == "awaiting_approval"


def test_ap_clerk_cannot_approve_or_reject(seeded):
    inv = by_file(seeded, "INV-2026-001_clean")
    with pytest.raises(PermissionDenied):
        approvals.approve(seeded, inv, "ap.clerk", "ap_clerk")
    with pytest.raises(PermissionDenied):
        approvals.reject(seeded, inv, "ap.clerk", "ap_clerk")


def test_finance_manager_approves_and_decision_is_recorded(seeded):
    approvals.set_threshold(seeded, "admin", "admin", 20000.0)  # AED 13,965 is within this limit
    inv = by_file(seeded, "INV-2026-001_clean")
    approvals.approve(seeded, inv, "fin.manager", "finance_manager", notes="Verified against GRN")
    assert inv.status == "approved"
    req = seeded.query(ApprovalRequest).filter(ApprovalRequest.invoice_id == inv.id).one()
    assert req.status == "closed" and req.decision == "approved"
    assert req.decided_by == "fin.manager" and req.decided_at is not None
    assert seeded.query(AuditLog).filter(AuditLog.action == "invoice_approved").count() == 1


def test_flagged_invoice_cannot_be_approved(seeded):
    inv = by_file(seeded, "INV-2026-002")
    with pytest.raises(WorkflowError, match="high or critical"):
        approvals.approve(seeded, inv, "fin.manager", "finance_manager")
    assert inv.status == "flagged"


def test_rejection_is_terminal(seeded):
    inv = by_file(seeded, "INV-2026-006")
    approvals.reject(seeded, inv, "fin.manager", "finance_manager", notes="No PO, not accepted")
    assert inv.status == "rejected"
    with pytest.raises(WorkflowError):
        approvals.reject(seeded, inv, "fin.manager", "finance_manager")
    with pytest.raises(WorkflowError):
        approvals.approve(seeded, inv, "admin", "admin")


def test_threshold_blocks_finance_manager_but_not_admin(seeded):
    approvals.set_threshold(seeded, "admin", "admin", 1000.0)
    inv = by_file(seeded, "INV-2026-001_clean")
    with pytest.raises(PermissionDenied, match="threshold"):
        approvals.approve(seeded, inv, "fin.manager", "finance_manager")
    approvals.approve(seeded, inv, "admin", "admin")
    assert inv.status == "approved"


def test_only_admin_can_change_threshold(seeded):
    with pytest.raises(PermissionDenied):
        approvals.set_threshold(seeded, "fin.manager", "finance_manager", 5.0)


def test_resolved_exception_is_honoured_on_reanalysis(seeded):
    from app.services import invoices
    inv = by_file(seeded, "INV-2026-002")
    disc = next(d for d in inv.discrepancies if d.type == "price_mismatch")
    with pytest.raises(PermissionDenied):
        approvals.resolve_discrepancy(seeded, disc, "ap.clerk", "ap_clerk", "Supplier confirmed price change")
    approvals.resolve_discrepancy(seeded, disc, "admin", "admin", "Supplier confirmed price change, PO amended")
    invoices.reanalyze(seeded, inv, "admin")
    assert "price_mismatch" not in discrepancy_types(inv)
    assert inv.status == "awaiting_approval"
