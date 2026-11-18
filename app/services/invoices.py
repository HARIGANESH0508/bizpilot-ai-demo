"""Invoice pipeline: upload -> OCR -> extraction -> validation -> supplier/PO match -> duplicates -> routing -> audit."""
import json
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Discrepancy, Invoice, InvoiceItem
from app.schemas import ExtractedInvoice, ExtractedItem
from app.services import approvals, audit, extraction, files, matching, ocr, validation
from app.services.duplicates import find_duplicates
from app.services.errors import NotFound, WorkflowError
from app.services.matching import Issue

DUPLICATE_SEVERITY = {"exact": "critical", "high": "high", "possible": "low"}


def get_invoice(session: Session, invoice_id: int) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise NotFound(f"Invoice {invoice_id} does not exist.")
    return invoice


def invoice_to_extracted(invoice: Invoice) -> ExtractedInvoice:
    return ExtractedInvoice(
        supplier_name=invoice.supplier_name,
        supplier_address=invoice.supplier_address,
        supplier_tax_id=invoice.supplier_tax_id,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        po_number=invoice.po_number_extracted,
        items=[ExtractedItem(product_code=i.product_code, description=i.description, quantity=i.quantity,
                             unit_price=i.unit_price, discount=i.discount, line_total=i.line_total)
               for i in invoice.items],
        discount_total=invoice.discount_total,
        subtotal=invoice.subtotal,
        vat_rate=invoice.vat_rate,
        vat_amount=invoice.vat_amount,
        grand_total=invoice.grand_total,
        overall_confidence=invoice.extraction_confidence,
        missing_fields=json.loads(invoice.missing_fields or "[]"),
        ocr_method=invoice.ocr_method or "",
    )


def _apply_extraction(invoice: Invoice, extracted: ExtractedInvoice) -> None:
    invoice.invoice_number = extracted.invoice_number
    invoice.supplier_name = extracted.supplier_name
    invoice.supplier_tax_id = extracted.supplier_tax_id
    invoice.supplier_address = extracted.supplier_address
    invoice.po_number_extracted = extracted.po_number
    invoice.invoice_date = extracted.invoice_date
    invoice.due_date = extracted.due_date
    invoice.currency = extracted.currency
    invoice.extraction_confidence = extracted.overall_confidence
    invoice.missing_fields = json.dumps(extracted.missing_fields)
    invoice.ocr_method = extracted.ocr_method or invoice.ocr_method

    invoice.items.clear()
    for idx, item in enumerate(extracted.items, start=1):
        invoice.items.append(InvoiceItem(
            line_no=idx, product_code=item.product_code, description=item.description,
            quantity=item.quantity, unit_price=item.unit_price, discount=item.discount,
            line_total=float(validation.money(item.quantity * item.unit_price - item.discount)),
        ))

    if extracted.items:
        totals = validation.compute_totals(extracted.items, extracted.discount_total, extracted.vat_rate)
        invoice.subtotal = totals["subtotal"]
        invoice.discount_total = extracted.discount_total
        invoice.vat_rate = totals["vat_rate"]
        invoice.vat_amount = totals["vat_amount"]
        invoice.grand_total = totals["grand_total"]
    else:
        invoice.grand_total = extracted.grand_total or 0.0
        invoice.subtotal = extracted.subtotal or 0.0
        invoice.vat_amount = extracted.vat_amount or 0.0
        invoice.vat_rate = extracted.vat_rate or 0.0


def analyze(
    session: Session,
    invoice: Invoice,
    actor: str,
    extracted_override: ExtractedInvoice | None = None,
) -> dict:
    """Run checks using fresh OCR extraction or explicitly supplied reviewed fields."""
    if invoice.status in approvals.TERMINAL_STATUSES:
        raise WorkflowError(f"Invoice is already {invoice.status} and cannot be re-analysed.")
    started = time.perf_counter()

    if extracted_override is None:
        if not invoice.ocr_text:
            try:
                text, method = ocr.extract_text(invoice.stored_path)
                invoice.ocr_text, invoice.ocr_method = text, method
            except ocr.OcrError as exc:
                return _route_ocr_failure(session, invoice, actor, str(exc))
        extracted = extraction.extract_invoice(invoice.ocr_text, invoice.ocr_method or "")
    else:
        # Reviewed values are the source of truth during re-validation. Never overwrite them with OCR.
        extracted = extracted_override
    _apply_extraction(invoice, extracted)

    issues: list[Issue] = [Issue(i.type, i.severity, i.message)
                           for i in validation.validate_invoice(extracted)]

    supplier = matching.resolve_supplier(session, extracted.supplier_name, extracted.supplier_tax_id)
    invoice.supplier_id = supplier.id if supplier else None

    duplicates = find_duplicates(
        session, sha256=invoice.file_sha256, supplier_id=invoice.supplier_id,
        supplier_name=invoice.supplier_name, invoice_number=invoice.invoice_number,
        grand_total=invoice.grand_total, invoice_date=invoice.invoice_date,
        exclude_invoice_id=invoice.id,
    )
    for dup in duplicates:
        issues.append(Issue("duplicate_invoice", DUPLICATE_SEVERITY[dup.confidence], dup.reason))

    po, match_issues = matching.match_invoice(session, invoice)
    issues.extend(match_issues)
    invoice.po_id = po.id if po else None
    session.flush()

    status = approvals.route(session, invoice, issues, {d.kind for d in duplicates})
    invoice.status = status
    invoice.risk_score = matching.risk_score(issues)
    if status in {"awaiting_approval", "flagged"}:
        approvals.ensure_open_request(session, invoice, requested_by=actor)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    audit.record(session, actor, "invoice_analyzed", "invoice", invoice.id, {
        "status": status, "risk_score": invoice.risk_score, "processing_ms": elapsed_ms,
        "issue_types": sorted({i.type for i in issues}), "ocr_method": invoice.ocr_method,
    })
    if duplicates:
        audit.record(session, actor, "duplicate_detected", "invoice", invoice.id,
                     {"findings": [d.reason for d in duplicates]})
    session.commit()
    return summarize(invoice)


def _route_ocr_failure(session: Session, invoice: Invoice, actor: str, reason: str) -> dict:
    invoice.status = "needs_information"
    invoice.missing_fields = json.dumps(["ocr_text"])
    invoice.risk_score = 0
    session.execute(Discrepancy.__table__.delete().where(
        (Discrepancy.invoice_id == invoice.id) & (Discrepancy.resolved == False)))  # noqa: E712
    session.add(Discrepancy(invoice_id=invoice.id, type="ocr_failed", severity="medium", message=reason))
    audit.record(session, actor, "ocr_failed", "invoice", invoice.id, {"reason": reason})
    session.commit()
    return summarize(invoice)


def process_upload(session: Session, *, filename: str, data: bytes, actor: str) -> dict:
    """Entry point for uploads. Exact duplicate files are returned without creating a new invoice."""
    ext = files.validate_upload(filename, data)
    sha, path = files.store_file(data, ext)

    existing = session.scalar(select(Invoice).where(Invoice.file_sha256 == sha))
    if existing:
        audit.record(session, actor, "duplicate_upload_blocked", "invoice", existing.id,
                     {"filename": filename})
        session.commit()
        return {"filename": filename, "status": "duplicate", "invoice_id": existing.id,
                "message": f"Identical file already exists as invoice #{existing.id}."}

    invoice = Invoice(original_filename=filename, file_sha256=sha, stored_path=path,
                      status="pending_review", uploaded_by=actor)
    session.add(invoice)
    session.flush()
    audit.record(session, actor, "invoice_uploaded", "invoice", invoice.id, {"filename": filename})
    result = analyze(session, invoice, actor)
    result["filename"] = filename
    return result


def reanalyze(session: Session, invoice: Invoice, actor: str) -> dict:
    """Re-run validation/matching against current reviewed fields without replacing them with OCR.

    To intentionally refresh OCR from the original file, use reprocess_source() instead.
    """
    return analyze(session, invoice, actor, extracted_override=invoice_to_extracted(invoice))


def reprocess_source(session: Session, invoice: Invoice, actor: str) -> dict:
    """Explicitly discard reviewed corrections and re-extract from the original uploaded file."""
    invoice.ocr_text = None
    session.flush()
    return analyze(session, invoice, actor)


def summarize(invoice: Invoice) -> dict:
    return {
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "supplier": invoice.supplier_name,
        "status": invoice.status,
        "grand_total": invoice.grand_total,
        "currency": invoice.currency,
        "risk_score": invoice.risk_score,
        "extraction_confidence": invoice.extraction_confidence,
        "missing_fields": json.loads(invoice.missing_fields or "[]"),
        "discrepancies": [serialize_discrepancy(d) for d in invoice.discrepancies],
    }


def serialize_discrepancy(d: Discrepancy) -> dict:
    return {"id": d.id, "type": d.type, "severity": d.severity, "message": d.message,
            "resolved": d.resolved, "resolution_note": d.resolution_note}


def serialize_invoice(invoice: Invoice, detail: bool = False) -> dict:
    data = {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "supplier_name": invoice.supplier_name,
        "supplier_id": invoice.supplier_id,
        "po_number": invoice.po_number_extracted,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "currency": invoice.currency,
        "subtotal": invoice.subtotal,
        "discount_total": invoice.discount_total,
        "vat_rate": invoice.vat_rate,
        "vat_amount": invoice.vat_amount,
        "grand_total": invoice.grand_total,
        "status": invoice.status,
        "risk_score": invoice.risk_score,
        "extraction_confidence": invoice.extraction_confidence,
        "missing_fields": json.loads(invoice.missing_fields or "[]"),
        "original_filename": invoice.original_filename,
        "uploaded_by": invoice.uploaded_by,
        "created_at": invoice.created_at.isoformat(),
    }
    if detail:
        data["items"] = [{"line_no": i.line_no, "product_code": i.product_code, "description": i.description,
                          "quantity": i.quantity, "unit_price": i.unit_price, "line_total": i.line_total}
                         for i in invoice.items]
        data["discrepancies"] = [serialize_discrepancy(d) for d in invoice.discrepancies]
        data["approvals"] = [{"id": a.id, "status": a.status, "decision": a.decision, "decided_by": a.decided_by,
                              "decided_at": a.decided_at.isoformat() if a.decided_at else None,
                              "reviewer_notes": a.reviewer_notes} for a in invoice.approvals]
    return data


def list_invoices(session: Session, status: str | None = None, limit: int = 100) -> list[Invoice]:
    query = select(Invoice).order_by(Invoice.id.desc()).limit(limit)
    if status:
        query = query.where(Invoice.status == status)
    return list(session.scalars(query).all())
