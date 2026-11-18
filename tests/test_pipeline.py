from app.models import Invoice
from app.services import invoices
from app.services import ocr
from app.services.duplicates import find_duplicates
from app.services.errors import ServiceError
from app.services.seed import text_to_pdf_bytes
from tests.conftest import by_file, discrepancy_types


def test_clean_invoice_routes_to_approval_queue(seeded):
    inv = by_file(seeded, "INV-2026-001_clean")
    assert inv.status == "awaiting_approval"
    assert discrepancy_types(inv) == set()
    assert inv.grand_total == 13965.00
    assert inv.supplier_id is not None and inv.po_id is not None


def test_nothing_is_auto_approved(seeded):
    statuses = {i.status for i in seeded.query(Invoice).all()}
    assert "approved" not in statuses


def test_price_mismatch_flagged(seeded):
    assert "price_mismatch" in discrepancy_types(by_file(seeded, "INV-2026-002"))


def test_overbilling_flagged(seeded):
    inv = by_file(seeded, "INV-2026-003")
    assert inv.status == "flagged"
    assert "overbilling" in discrepancy_types(inv)


def test_duplicate_invoice_number_flagged(seeded):
    inv = by_file(seeded, "INV-2026-001_resent")
    assert inv.status == "flagged"
    assert "duplicate_invoice" in discrepancy_types(inv)
    assert any("already invoiced" in d.message for d in inv.discrepancies)


def test_vat_error_detected_and_totals_recomputed(seeded):
    inv = by_file(seeded, "INV-2026-005")
    assert {"tax_calculation_error", "total_mismatch"} <= discrepancy_types(inv)
    assert inv.vat_amount == 45.00
    assert inv.grand_total == 945.00


def test_missing_po_flagged(seeded):
    assert "missing_purchase_order" in discrepancy_types(by_file(seeded, "INV-2026-006"))


def test_exact_reupload_is_blocked_without_new_record(seeded):
    data = text_to_pdf_bytes(open("data/samples/invoices/INV-2026-006_no_po.txt", encoding="utf-8").read())
    invoices.process_upload(seeded, filename="x.pdf", data=data, actor="t")
    count_after_first = seeded.query(Invoice).count()
    second = invoices.process_upload(seeded, filename="x.pdf", data=data, actor="t")
    assert second["status"] == "duplicate"
    assert seeded.query(Invoice).count() == count_after_first


def test_fuzzy_invoice_number_is_only_possible(seeded):
    clean = by_file(seeded, "INV-2026-001_clean")
    findings = find_duplicates(seeded, sha256=None, supplier_id=clean.supplier_id, supplier_name=None,
                               invoice_number="INV-2026-0011", grand_total=13965.00,
                               invoice_date=clean.invoice_date)
    assert any(f.kind == "fuzzy_invoice_number" and f.confidence == "possible" for f in findings)


def test_ocr_failure_routes_to_needs_information(db, monkeypatch):
    def boom(_path):
        raise ocr.OcrError("Tesseract not installed")
    monkeypatch.setattr(ocr, "extract_text", boom)
    data = text_to_pdf_bytes("scanned image without text layer")
    result = invoices.process_upload(db, filename="scan.pdf", data=data, actor="t")
    assert result["status"] == "needs_information"
    inv = db.query(Invoice).one()
    assert "ocr_failed" in discrepancy_types(inv)


def test_rejects_unsupported_upload(db):
    import pytest
    with pytest.raises(ServiceError):
        invoices.process_upload(db, filename="virus.exe", data=b"MZ", actor="t")


def test_reanalysis_preserves_manual_corrections(seeded):
    inv = by_file(seeded, "INV-2026-001_clean")
    original_ocr = inv.ocr_text
    inv.invoice_number = "INV-CORRECTED-9001"
    inv.supplier_name = "Gulf Office Supplies LLC - Reviewed"
    inv.po_number_extracted = "PO-2026-0001"
    invoices.reanalyze(seeded, inv, "reviewer")
    assert inv.invoice_number == "INV-CORRECTED-9001"
    assert inv.supplier_name == "Gulf Office Supplies LLC - Reviewed"
    assert inv.po_number_extracted == "PO-2026-0001"
    assert inv.ocr_text == original_ocr


def test_reanalysis_does_not_replace_corrected_invoice_number(seeded):
    inv = by_file(seeded, "INV-2026-001_clean")
    inv.invoice_number = "INV-REVIEWED-123"
    invoices.reanalyze(seeded, inv, "reviewer")
    assert inv.invoice_number == "INV-REVIEWED-123"
