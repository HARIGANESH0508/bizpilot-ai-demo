from datetime import date

from app.services.extraction import extract_invoice, parse_date

SAMPLE = """TAX INVOICE
Supplier: Al Noor Office Supplies LLC
Supplier address: Office 204, Business Bay, Dubai, UAE
VAT Reg No: 100234567800003
Invoice No: INV-2026-001
Invoice Date: 2026-10-02
Due Date: 2026-11-01
Currency: AED
PO Number: PO-2026-0101

CODE | DESCRIPTION | QTY | UNIT PRICE | LINE TOTAL
OFF-CHR-01 | Ergonomic office chair | 10 | 850.00 | 8500.00
OFF-DSK-02 | Standing desk 160cm | 4 | 1200.00 | 4800.00

Subtotal: 13300.00
VAT (5%): 665.00
Grand Total: 13965.00
"""


def test_extracts_header_fields():
    e = extract_invoice(SAMPLE, "pdf-text")
    assert e.supplier_name == "Al Noor Office Supplies LLC"
    assert e.supplier_tax_id == "100234567800003"
    assert e.invoice_number == "INV-2026-001"
    assert e.invoice_date == date(2026, 10, 2)
    assert e.due_date == date(2026, 11, 1)
    assert e.currency == "AED"
    assert e.po_number == "PO-2026-0101"


def test_extracts_totals_and_vat_rate():
    e = extract_invoice(SAMPLE)
    assert e.subtotal == 13300.00
    assert e.vat_amount == 665.00
    assert e.vat_rate == 5.0
    assert e.grand_total == 13965.00


def test_extracts_line_items_with_codes():
    e = extract_invoice(SAMPLE)
    assert len(e.items) == 2
    assert e.items[0].product_code == "OFF-CHR-01"
    assert e.items[0].description == "Ergonomic office chair"
    assert e.items[0].quantity == 10 and e.items[0].unit_price == 850.00
    assert e.items[1].line_total == 4800.00


def test_complete_invoice_has_full_confidence_and_no_missing_fields():
    e = extract_invoice(SAMPLE)
    assert e.missing_fields == []
    assert e.overall_confidence > 0.9


def test_missing_total_is_reported_and_lowers_confidence():
    text = SAMPLE.replace("Grand Total: 13965.00", "")
    e = extract_invoice(text)
    assert "grand_total" in e.missing_fields
    assert e.overall_confidence < 1.0


def test_invoice_number_not_confused_with_invoice_date():
    e = extract_invoice(SAMPLE.replace("Invoice No: INV-2026-001", ""))
    assert e.invoice_number is None
    assert e.invoice_date == date(2026, 10, 2)


def test_parse_date_formats():
    assert parse_date("2026-10-02") == date(2026, 10, 2)
    assert parse_date("02/10/2026") == date(2026, 10, 2)
    assert parse_date("2 Oct 2026") == date(2026, 10, 2)
    assert parse_date("not a date") is None


def test_currency_validation_rejects_non_iso():
    from pydantic import ValidationError
    from app.schemas import ExtractedInvoice
    try:
        ExtractedInvoice(currency="DIRHAMS")
    except ValidationError:
        return
    raise AssertionError("expected ValidationError")
