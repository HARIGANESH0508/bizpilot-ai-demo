"""Demo data loader. Creates users, procurement master data and sample invoices.

Sample invoices are rendered to PDF and pushed through the real upload pipeline, so the demo exercises
exactly the code path a production upload would take.
"""
import json
from datetime import date

import fitz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import BASE_DIR, settings
from app.models import GoodsReceipt, GoodsReceiptItem, PurchaseOrder, PurchaseOrderItem, Supplier, User
from app.security import hash_password
from app.services import audit, invoices

SAMPLES = BASE_DIR / "data" / "samples"

DEMO_USERS = [
    ("admin", "Ahmed Admin", "admin"),
    ("fin.manager", "Layla Finance Manager", "finance_manager"),
    ("ap.clerk", "Omar AP Clerk", "ap_clerk"),
    ("viewer", "Read Only Viewer", "viewer"),
]


def ensure_demo_users(session: Session) -> int:
    """Ensure documented demo accounts always use the current configured demo password."""
    created = 0
    current_password_hash = hash_password(settings.demo_password)
    for username, full_name, role in DEMO_USERS:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            session.add(User(username=username, full_name=full_name, role=role,
                             password_hash=current_password_hash))
            created += 1
        else:
            # These are dedicated demo accounts; sync their password to Streamlit Secrets.
            user.password_hash = current_password_hash
    session.commit()
    return created


def text_to_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 50
    for line in text.splitlines():
        page.insert_text((50, y), line, fontsize=10, fontname="cour")
        y += 14
    data = doc.tobytes()
    doc.close()
    return data


def _load_masters(session: Session) -> dict:
    suppliers = {}
    for row in json.loads((SAMPLES / "procurement" / "suppliers.json").read_text(encoding="utf-8")):
        s = Supplier(name=row["name"], tax_id=row["tax_id"], address=row["address"])
        session.add(s)
        session.flush()
        suppliers[s.name] = s

    pos = {}
    for row in json.loads((SAMPLES / "procurement" / "purchase_orders.json").read_text(encoding="utf-8")):
        po = PurchaseOrder(po_number=row["po_number"], supplier_id=suppliers[row["supplier"]].id,
                           order_date=date.fromisoformat(row["order_date"]), currency=row["currency"])
        for n, item in enumerate(row["items"], start=1):
            po.items.append(PurchaseOrderItem(line_no=n, **item))
        session.add(po)
        session.flush()
        pos[po.po_number] = po

    for row in json.loads((SAMPLES / "procurement" / "goods_receipts.json").read_text(encoding="utf-8")):
        grn = GoodsReceipt(grn_number=row["grn_number"], po_id=pos[row["po_number"]].id,
                           receipt_date=date.fromisoformat(row["receipt_date"]))
        for item in row["items"]:
            grn.items.append(GoodsReceiptItem(**item))
        session.add(grn)
    session.commit()
    return {"suppliers": len(suppliers), "purchase_orders": len(pos)}


def seed_demo(session: Session, actor: str = "seed") -> dict:
    """Idempotent: does nothing if suppliers already exist. Returns counts and per-invoice results."""
    users_created = ensure_demo_users(session)
    if session.scalar(select(func.count(Supplier.id))) > 0:
        return {"status": "skipped", "reason": "demo data already present", "users_created": users_created}

    masters = _load_masters(session)
    results = []
    out_dir = SAMPLES / "invoices"
    for txt in sorted(out_dir.glob("*.txt")):
        pdf_name = txt.stem + ".pdf"
        pdf_bytes = text_to_pdf_bytes(txt.read_text(encoding="utf-8"))
        (out_dir / pdf_name).write_bytes(pdf_bytes)  # kept on disk so the demo files can be opened directly
        results.append(invoices.process_upload(session, filename=pdf_name, data=pdf_bytes, actor=actor))

    audit.record(session, actor, "demo_data_loaded", "system", "demo", {"invoices": len(results)})
    session.commit()
    return {"status": "seeded", "users_created": users_created, **masters, "invoices": results}
