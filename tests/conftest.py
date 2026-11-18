"""Test environment: isolated temp database and upload dir, offline LLM, fast password hashing.

Environment variables must be set before any app module is imported, because settings are read at import time.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="bizpilot-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["UPLOAD_DIR"] = f"{_TMP}/uploads"
os.environ["OLLAMA_ENABLED"] = "false"
os.environ["PBKDF2_ITERATIONS"] = "1000"
os.environ["N8N_WEBHOOK_SECRET"] = "test-secret"
os.environ["DEMO_PASSWORD"] = "Test#Password1"
os.environ["APPROVAL_THRESHOLD"] = "10000"

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models import Invoice  # noqa: E402
from app.services.seed import ensure_demo_users, seed_demo  # noqa: E402


@pytest.fixture
def db():
    Base.metadata.drop_all(engine)
    init_db()
    session = SessionLocal()
    ensure_demo_users(session)
    yield session
    session.close()


@pytest.fixture
def seeded(db):
    seed_demo(db)
    return db


def by_file(session, fragment: str) -> Invoice:
    inv = session.query(Invoice).filter(Invoice.original_filename.contains(fragment)).order_by(Invoice.id).first()
    assert inv is not None, f"no invoice whose file contains {fragment!r}"
    return inv


def discrepancy_types(inv: Invoice) -> set[str]:
    return {d.type for d in inv.discrepancies if not d.resolved}
