from app.models import AuditLog
from app.services.agent import AgentSession, BusinessAgent, route
from tests.conftest import by_file


def _agent(db, role="finance_manager"):
    return BusinessAgent(db, AgentSession(actor="tester", role=role))


def test_routing_table():
    assert route("show flagged invoices") == "get_flagged_invoices"
    assert route("match invoice 2") == "match_purchase_order"
    assert route("what are the pending approvals") == "get_pending_invoices"
    assert route("approve invoice 1") == "approve_invoice"
    assert route("tell me a joke") is None


def test_flagged_query_answers_from_database(seeded):
    reply = _agent(seeded).handle("show flagged invoices")
    assert reply["tool"] == "get_flagged_invoices"
    assert isinstance(reply["data"], list) and len(reply["data"]) >= 4
    assert "Found" in reply["answer"]


def test_match_by_invoice_number(seeded):
    reply = _agent(seeded).handle("match INV-2026-002")
    assert reply["tool"] == "match_purchase_order"
    assert any(i["type"] == "price_mismatch" for i in reply["data"]["issues"])


def test_write_action_requires_confirmation(seeded):
    from app.services import approvals
    approvals.set_threshold(seeded, "admin", "admin", 20000.0)
    agent = _agent(seeded)
    inv = by_file(seeded, "INV-2026-001_clean")
    reply = agent.handle(f"approve invoice {inv.id}")
    assert reply["requires_confirmation"] is True
    assert inv.status == "awaiting_approval"  # nothing happened yet
    done = agent.handle(f"confirm {reply['token']}")
    assert done["tool"] == "approve_invoice"
    assert inv.status == "approved"


def test_wrong_confirmation_code_does_nothing(seeded):
    reply = _agent(seeded).handle("confirm abc123")
    assert "No pending action" in reply["answer"]


def test_agent_respects_roles_on_write_tools(seeded):
    viewer = _agent(seeded, role="viewer")
    inv = by_file(seeded, "INV-2026-001_clean")
    token = viewer.handle(f"approve invoice {inv.id}")["token"]
    result = viewer.handle(f"confirm {token}")
    assert "cannot approve" in result["answer"]
    assert inv.status == "awaiting_approval"


def test_unknown_request_gets_helpful_fallback(seeded):
    reply = _agent(seeded).handle("write me a poem")
    assert reply["tool"] is None
    assert "invoice" in reply["answer"].lower()


def test_missing_invoice_reference_asks_for_one(seeded):
    reply = _agent(seeded).handle("validate this invoice")
    assert "Which invoice" in reply["answer"]


def test_read_tools_are_audited(seeded):
    _agent(seeded).handle("show approved invoices")
    assert seeded.query(AuditLog).filter(AuditLog.action == "agent_tool_executed").count() >= 1
