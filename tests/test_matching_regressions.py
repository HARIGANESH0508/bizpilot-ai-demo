from app.models import InvoiceItem, PurchaseOrder, PurchaseOrderItem, GoodsReceipt, GoodsReceiptItem
from app.services.matching import match_invoice
from tests.conftest import by_file


def test_two_invoice_rows_for_same_po_item_are_aggregated(seeded):
    inv = by_file(seeded, "INV-2026-001_clean")
    # The clean invoice contains an existing line. Add another row for the same PO item
    # so that each row individually is within the ordered quantity but their sum is not.
    po = seeded.get(PurchaseOrder, inv.po_id)
    po_item = po.items[0]
    inv.items.append(InvoiceItem(line_no=99, product_code=po_item.product_code,
                                 description=po_item.description, quantity=po_item.quantity,
                                 unit_price=po_item.unit_price, line_total=po_item.quantity * po_item.unit_price))
    seeded.flush()
    _, issues = match_invoice(seeded, inv)
    assert any(i.type == "quantity_mismatch" and "cumulative invoiced" in i.message for i in issues)


def test_below_po_price_is_also_flagged(seeded):
    inv = by_file(seeded, "INV-2026-001_clean")
    inv.items[0].unit_price = inv.items[0].unit_price * 0.90
    seeded.flush()
    _, issues = match_invoice(seeded, inv)
    assert any(i.type == "price_mismatch" and "below" in i.message for i in issues)
