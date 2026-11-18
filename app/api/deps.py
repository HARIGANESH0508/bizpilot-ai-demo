"""Authentication dependency: bearer tokens stored as SHA-256 hashes, with expiry and account status checks."""
from datetime import timedelta

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiToken, User, utcnow
from app.security import can, sha256_text
from app.services.errors import PermissionDenied, Unauthorized

bearer = HTTPBearer(auto_error=False)


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_session),
) -> User:
    if creds is None:
        raise Unauthorized("Missing bearer token.")
    token = db.scalar(select(ApiToken).where(ApiToken.token_hash == sha256_text(creds.credentials)))
    if token is None or token.expires_at < utcnow():
        raise Unauthorized("Invalid or expired token.")
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Account is disabled.")
    return user


def require(permission: str):
    def checker(user: User = Depends(current_user)) -> User:
        if not can(user.role, permission):
            raise PermissionDenied(f"Role '{user.role}' lacks the '{permission}' permission.")
        return user
    return checker


def new_expiry() -> object:
    from app.config import settings
    return utcnow() + timedelta(hours=settings.token_ttl_hours)
