import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.services.seed import text_to_pdf_bytes
from tests.conftest import by_file


@pytest.fixture
def client(seeded):
    from app.services import approvals
    approvals.set_threshold(seeded, "admin", "admin", 20000.0)
    return TestClient(app)


def _login(client, username, password="Test#Password1"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200 and resp.json()["database"] == "ok"


def test_wrong_password_returns_standard_error(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_endpoints_require_token(client):
    assert client.get("/api/invoices").status_code == 401


def test_viewer_can_read_but_not_upload(client):
    headers = _login(client, "viewer")
    assert client.get("/api/invoices", headers=headers).status_code == 200
    resp = client.post("/api/invoices/upload", headers=headers,
                       files={"files": ("a.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_upload_pipeline_via_api(client, seeded):
    headers = _login(client, "ap.clerk")
    text = open("data/samples/invoices/INV-2026-001_clean.txt", encoding="utf-8").read()
    fresh = text.replace("INV-2026-001", "INV-2026-900").replace("Remarks", "x")
    pdf = text_to_pdf_bytes(fresh)
    resp = client.post("/api/invoices/upload", headers=headers,
                       files={"files": ("api_test.pdf", pdf, "application/pdf")})
    assert resp.status_code == 201
    result = resp.json()["results"][0]
    assert result["status"] in {"awaiting_approval", "flagged"}
    assert result["invoice_number"] == "INV-2026-900"


def test_rejects_wrong_file_type_per_file(client, seeded):
    headers = _login(client, "ap.clerk")
    resp = client.post("/api/invoices/upload", headers=headers,
                       files={"files": ("evil.exe", b"MZ..", "application/octet-stream")})
    assert resp.json()["results"][0]["status"] == "rejected"


def test_cannot_approve_flagged_invoice_via_api(client, seeded):
    headers = _login(client, "fin.manager")
    inv = by_file(seeded, "INV-2026-002")
    resp = client.post(f"/api/invoices/{inv.id}/approve", headers=headers, json={"notes": "ok"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state"


def test_approve_clean_invoice_via_api(client, seeded):
    headers = _login(client, "fin.manager")
    inv = by_file(seeded, "INV-2026-001_clean")
    resp = client.post(f"/api/invoices/{inv.id}/approve", headers=headers, json={"notes": "Matched to GRN"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_match_endpoint_is_read_only(client, seeded):
    headers = _login(client, "viewer")
    inv = by_file(seeded, "INV-2026-003")
    resp = client.post(f"/api/invoices/{inv.id}/match", headers=headers)
    assert resp.status_code == 200
    assert any(i["type"] == "overbilling" for i in resp.json()["issues"])
    assert inv.status == "flagged"


def test_n8n_webhook_requires_secret_and_is_idempotent(client, seeded):
    inv = by_file(seeded, "INV-2026-001_clean")
    body = {"invoice_id": inv.id, "source": "n8n"}
    assert client.post("/api/webhooks/n8n/invoice-event", json=body,
                       headers={"X-Event-Id": "evt-1", "X-Webhook-Secret": "wrong"}).status_code == 401
    ok = client.post("/api/webhooks/n8n/invoice-event", json=body,
                     headers={"X-Event-Id": "evt-2", "X-Webhook-Secret": "test-secret"})
    assert ok.status_code == 200 and ok.json()["status"] == "completed"
    again = client.post("/api/webhooks/n8n/invoice-event", json=body,
                        headers={"X-Event-Id": "evt-2", "X-Webhook-Secret": "test-secret"})
    assert again.json()["status"] == "duplicate_event"


def test_analytics_and_audit_endpoints(client, seeded):
    headers = _login(client, "admin")
    analytics = client.get("/api/analytics", headers=headers).json()
    assert analytics["summary"]["total_invoices"] == 6
    audit = client.get("/api/audit?limit=5", headers=headers).json()
    assert len(audit) <= 5
    assert json.dumps(audit)  # serialisable
