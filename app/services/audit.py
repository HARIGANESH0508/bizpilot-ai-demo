"""Append-only audit trail. Callers commit together with the business change they describe."""
import json

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(session: Session, actor: str, action: str, entity_type: str, entity_id, details: dict | None = None) -> None:
    session.add(AuditLog(
        actor=actor or "system",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=json.dumps(details or {}, default=str),
    ))
