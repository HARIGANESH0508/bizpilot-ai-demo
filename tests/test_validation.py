import pytest

from app.schemas import ExtractedInvoice, ExtractedItem
from app.services import files
from app.services.errors import FileRejected
from app.services.validation import compute_totals, validate_invoice


def _items():
    return [ExtractedItem(product_code="A", description="a", quantity=10, unit_price=850, line_total=8500),
            ExtractedItem(product_code="B", description="b", quantity=4, unit_price=1200, line_total=4800)]


def test_totals_are_recomputed_independently():
    totals = compute_totals(_items(), 0.0, 5.0)
    assert totals["subtotal"] == 13300.00
    assert totals["vat_amount"] == 665.00
    assert totals["grand_total"] == 13965.00


def test_discount_is_applied_before_vat():
    totals = compute_totals(_items(), discount_total=300.0, vat_rate=5.0)
    assert totals["subtotal"] == 13000.00
    assert totals["vat_amount"] == 650.00


def test_clean_invoice_has_no_issues():
    e = ExtractedInvoice(items=_items(), subtotal=13300, vat_amount=665, grand_total=13965, vat_rate=5)
    assert validate_invoice(e) == []


def test_vat_and_total_errors_are_detected():
    # Items sum to 13,300. VAT should be 665 and total 13,965, but the document states 40 and 13,340.
    e = ExtractedInvoice(items=_items(), subtotal=13300, vat_amount=40, grand_total=13340, vat_rate=5)
    types = {i.type for i in validate_invoice(e)}
    assert {"tax_calculation_error", "total_mismatch"} <= types
    assert "subtotal_mismatch" not in types  # the stated subtotal is correct


def test_line_total_mismatch_detected():
    bad = ExtractedItem(product_code="A", description="a", quantity=10, unit_price=850, line_total=9000)
    e = ExtractedInvoice(items=[bad], subtotal=8500, vat_amount=425, grand_total=8925, vat_rate=5)
    assert "line_total_mismatch" in {i.type for i in validate_invoice(e)}


def test_due_before_invoice_date_is_flagged():
    from datetime import date
    e = ExtractedInvoice(invoice_date=date(2026, 10, 2), due_date=date(2026, 9, 1), items=_items(), vat_rate=5)
    assert any(i.type == "date_inconsistency" for i in validate_invoice(e))


def test_accepts_valid_pdf_signature():
    assert files.validate_upload("inv.pdf", b"%PDF-1.4 content") == ".pdf"


@pytest.mark.parametrize("name, data, fragment", [
    ("inv.exe", b"MZ....", "Unsupported"),
    ("inv.pdf", b"", "empty"),
    ("inv.pdf", b"<html>not a pdf</html>", "does not match"),
    ("inv.png", b"%PDF-1.4", "does not match"),
])
def test_rejects_bad_uploads(name, data, fragment):
    with pytest.raises(FileRejected, match=fragment):
        files.validate_upload(name, data)


def test_rejects_oversized_upload():
    data = b"%PDF" + b"0" * (10 * 1024 * 1024 + 1)
    with pytest.raises(FileRejected, match="limit"):
        files.validate_upload("big.pdf", data)
