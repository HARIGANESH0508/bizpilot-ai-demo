"""Create tables and demo users. Safe to run repeatedly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.services.seed import ensure_demo_users  # noqa: E402

if __name__ == "__main__":
    init_db()
    with SessionLocal() as session:
        created = ensure_demo_users(session)
    print(f"Database ready. Demo users created: {created}")
