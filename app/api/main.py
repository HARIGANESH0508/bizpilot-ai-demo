"""FastAPI application. All business rules live in app/services; these handlers only translate HTTP."""
import hmac
import json
import logging

from fastapi import Depends, FastAPI, File, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.deps import new_expiry, require
from app.config import settings
from app.db import get_session, init_db
from app.models import ApiToken, AuditLog, Discrepancy, PurchaseOrder, Supplier, User, WorkflowExecution
from app.schemas import DecisionRequest, LoginRequest, ResolveRequest
from app.security import new_token, verify_password
from app.services import analytics, approvals, audit, invoices, llm, matching
from app.services.errors import NotFound, ServiceError, Unauthorized
from app.services.errors import FileRejected

log = logging.getLogger("bizpilot.api")

app = FastAPI(
    title="BizPilot AI API",
    version=__version__,
    description="Invoice ingestion, extraction, three-way matching, duplicate detection, approvals and audit.",
)


def error_body(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError):
    return error_body(exc.code, exc.message, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return error_body("validation_error", details, 422)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException):
    return error_body("http_error", str(exc.detail), exc.status_code)


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    log.exception("Unhandled error: %s", exc)  # full trace goes to logs, never to the client
    return error_body("internal_error", "An unexpected error occurred.", 500)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---- health -------------------------------------------------------------

@app.get("/health", tags=["system"])
def health(db: Session = Depends(get_session)):
    db_ok = True
    try:
        db.execute(select(1))
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "version": __version__,
            "database": "ok" if db_ok else "error"}


@app.get("/health/details", tags=["system"], dependencies=[Depends(require("admin"))])
def health_details():
    from app.services.ocr import _pytesseract
    try:
        tesseract = _pytesseract().get_tesseract_version()
        tesseract_status = {"ok": True, "version": str(tesseract)}
    except Exception as exc:
        tesseract_status = {"ok": False, "error": str(exc)[:200]}
    return {"ollama": llm.ollama_health(), "tesseract": tesseract_status,
            "upload_dir": str(settings.upload_dir)}


# ---- auth ---------------------------------------------------------------

@app.post("/api/auth/login", tags=["auth"])
def login(body: LoginRequest, db: Session = Depends(get_session)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise Unauthorized("Invalid username or password.")
    raw, digest = new_token()
    db.add(ApiToken(token_hash=digest, user_id=user.id, expires_at=new_expiry()))
    audit.record(db, user.username, "login", "user", user.id)
    db.commit()
    return {"access_token": raw, "token_type": "bearer", "role": user.role}


# ---- invoices -----------------------------------------------------------

@app.post("/api/invoices/upload", tags=["invoices"], status_code=201)
async def upload_invoices(
    files: list[UploadFile] = File(...),
    user: User = Depends(require("upload")),
    db: Session = Depends(get_session),
):
    limit = settings.max_upload_mb * 1024 * 1024
    results = []
    for f in files:
        data = await f.read(limit + 1)
        if len(data) > limit:
            results.append({"filename": f.filename, "status": "rejected",
                            "message": f"File exceeds the {settings.max_upload_mb} MB limit."})
            continue
        try:
            results.append(invoices.process_upload(db, filename=f.filename or "", data=data, actor=user.username))
        except FileRejected as exc:
            db.rollback()
            results.append({"filename": f.filename, "status": "rejected", "message": exc.message})
    return {"results": results}


@app.get("/api/invoices", tags=["invoices"])
def list_invoices(status: str | None = None, limit: int = 100,
                  user: User = Depends(require("read")), db: Session = Depends(get_session)):
    limit = max(1, min(limit, 500))
    return [invoices.serialize_invoice(i) for i in invoices.list_invoices(db, status, limit)]


@app.get("/api/invoices/{invoice_id}", tags=["invoices"])
def get_invoice(invoice_id: int, user: User = Depends(require("read")), db: Session = Depends(get_session)):
    return invoices.serialize_invoice(invoices.get_invoice(db, invoice_id), detail=True)


@app.post("/api/invoices/{invoice_id}/validate", tags=["invoices"])
def validate_invoice_endpoint(invoice_id: int, user: User = Depends(require("upload")),
                              db: Session = Depends(get_session)):
    """Re-runs the full pipeline (validation, duplicates, matching, routing) and saves the result."""
    inv = invoices.get_invoice(db, invoice_id)
    return invoices.reanalyze(db, inv, user.username)


@app.post("/api/invoices/{invoice_id}/match", tags=["invoices"])
def match_invoice_endpoint(invoice_id: int, user: User = Depends(require("read")),
                           db: Session = Depends(get_session)):
    """Read-only three-way match. Does not change invoice status."""
    inv = invoices.get_invoice(db, invoice_id)
    po, issues = matching.match_invoice(db, inv)
    return {"invoice_id": inv.id, "purchase_order": po.po_number if po else None,
            "risk_score": matching.risk_score(issues),
            "issues": [{"type": i.type, "severity": i.severity, "message": i.message} for i in issues]}


@app.post("/api/invoices/{invoice_id}/approve", tags=["approvals"])
def approve_invoice_endpoint(invoice_id: int, body: DecisionRequest | None = None,
                             user: User = Depends(require("approve")), db: Session = Depends(get_session)):
    inv = approvals.approve(db, invoices.get_invoice(db, invoice_id), user.username, user.role,
                            (body.notes if body else ""))
    return invoices.serialize_invoice(inv)


@app.post("/api/invoices/{invoice_id}/reject", tags=["approvals"])
def reject_invoice_endpoint(invoice_id: int, body: DecisionRequest | None = None,
                            user: User = Depends(require("reject")), db: Session = Depends(get_session)):
    inv = approvals.reject(db, invoices.get_invoice(db, invoice_id), user.username, user.role,
                           (body.notes if body else ""))
    return invoices.serialize_invoice(inv)


@app.post("/api/invoices/{invoice_id}/request-info", tags=["approvals"])
def request_info_endpoint(invoice_id: int, body: DecisionRequest | None = None,
                          user: User = Depends(require("review")), db: Session = Depends(get_session)):
    inv = approvals.request_information(db, invoices.get_invoice(db, invoice_id), user.username, user.role,
                                        (body.notes if body else ""))
    return invoices.serialize_invoice(inv)


@app.post("/api/discrepancies/{discrepancy_id}/resolve", tags=["approvals"])
def resolve_endpoint(discrepancy_id: int, body: ResolveRequest, user: User = Depends(require("resolve_discrepancy")),
                     db: Session = Depends(get_session)):
    d = db.get(Discrepancy, discrepancy_id)
    if d is None:
        raise NotFound(f"Discrepancy {discrepancy_id} does not exist.")
    approvals.resolve_discrepancy(db, d, user.username, user.role, body.note)
    return invoices.serialize_discrepancy(d)


# ---- reference data -----------------------------------------------------

@app.get("/api/purchase-orders", tags=["procurement"])
def list_pos(user: User = Depends(require("read")), db: Session = Depends(get_session)):
    rows = db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.po_number)).all()
    return [{"po_number": p.po_number, "supplier": p.supplier.name, "order_date": p.order_date.isoformat(),
             "currency": p.currency, "status": p.status, "lines": len(p.items)} for p in rows]


@app.get("/api/suppliers", tags=["procurement"])
def list_suppliers(user: User = Depends(require("read")), db: Session = Depends(get_session)):
    rows = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    return [{"id": s.id, "name": s.name, "tax_id": s.tax_id, "address": s.address, "active": s.is_active}
            for s in rows]


@app.get("/api/audit", tags=["audit"])
def list_audit(limit: int = 100, invoice_id: int | None = None,
               user: User = Depends(require("read")), db: Session = Depends(get_session)):
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(max(1, min(limit, 500)))
    if invoice_id is not None:
        query = query.where(AuditLog.entity_type == "invoice", AuditLog.entity_id == str(invoice_id))
    return [{"id": r.id, "at": r.created_at.isoformat(), "actor": r.actor, "action": r.action,
             "entity_type": r.entity_type, "entity_id": r.entity_id, "details": json.loads(r.details or "{}")}
            for r in db.scalars(query).all()]


@app.get("/api/analytics", tags=["analytics"])
def get_analytics(user: User = Depends(require("read")), db: Session = Depends(get_session)):
    return analytics.full_report(db)


# ---- n8n integration ----------------------------------------------------

@app.post("/api/webhooks/n8n/invoice-event", tags=["workflow"])
def n8n_invoice_event(
    payload: dict,
    x_event_id: str = Header(..., alias="X-Event-Id"),
    x_webhook_secret: str = Header("", alias="X-Webhook-Secret"),
    db: Session = Depends(get_session),
):
    """Triggered by n8n after an invoice is received. Idempotent via X-Event-Id; safe to retry."""
    if not settings.webhook_secret or not hmac.compare_digest(x_webhook_secret, settings.webhook_secret):
        raise Unauthorized("Invalid webhook secret.")

    existing = db.scalar(select(WorkflowExecution).where(WorkflowExecution.event_id == x_event_id))
    if existing:
        return {"status": "duplicate_event", "event_id": x_event_id, "previous_status": existing.status}

    execution = WorkflowExecution(event_id=x_event_id, workflow_name="invoice_received",
                                  status="received", payload=json.dumps(payload, default=str))
    db.add(execution)
    db.commit()

    try:
        invoice_id = int(payload.get("invoice_id"))
        inv = invoices.get_invoice(db, invoice_id)
        result = invoices.reanalyze(db, inv, actor="n8n")
        execution.status, execution.invoice_id = "completed", inv.id
        db.commit()
        return {"status": "completed", "event_id": x_event_id, "result": result}
    except (ServiceError, TypeError, ValueError) as exc:
        db.rollback()
        execution = db.scalar(select(WorkflowExecution).where(WorkflowExecution.event_id == x_event_id))
        execution.status, execution.error = "failed", str(getattr(exc, "message", exc))[:500]
        db.commit()
        return JSONResponse(status_code=422, content={"status": "failed", "event_id": x_event_id,
                                                      "error": execution.error})
