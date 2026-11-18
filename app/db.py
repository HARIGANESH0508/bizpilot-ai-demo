"""Database engine and session management. SQLite by default; any SQLAlchemy URL works (e.g. PostgreSQL)."""
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    future=True,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Services commit explicitly; close() rolls back anything uncommitted."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Idempotently create all tables. Use Alembic for schema changes after first release."""
    from app import models  # noqa: F401  (register models)

    if _is_sqlite:
        db_path = make_url(settings.database_url).database
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
