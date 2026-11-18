"""Load procurement master data and sample invoices through the real pipeline."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.services.seed import seed_demo  # noqa: E402

if __name__ == "__main__":
    init_db()
    with SessionLocal() as session:
        result = seed_demo(session)
    summary = {k: v for k, v in result.items() if k != "invoices"}
    summary["invoices"] = [{"file": r.get("filename"), "status": r.get("status"),
                            "invoice": r.get("invoice_number")} for r in result.get("invoices", [])]
    print(json.dumps(summary, indent=2, default=str))
