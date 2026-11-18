"""Password hashing (PBKDF2-SHA256, standard library only), tokens and role permissions."""
import hashlib
import hmac
import secrets

from app.config import settings

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "read": {"admin", "finance_manager", "ap_clerk", "viewer"},
    "upload": {"admin", "finance_manager", "ap_clerk"},
    "review": {"admin", "finance_manager", "ap_clerk"},       # request information, create approval request
    "approve": {"admin", "finance_manager"},
    "reject": {"admin", "finance_manager"},
    "resolve_discrepancy": {"admin", "finance_manager"},
    "approve_above_threshold": {"admin"},
    "admin": {"admin"},                                        # thresholds, demo data, user management
}


def can(role: str, permission: str) -> bool:
    return role in ROLE_PERMISSIONS.get(permission, set())


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = settings.pbkdf2_iterations
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_token() -> tuple[str, str]:
    """Returns (raw_token_for_client, sha256_hash_stored_in_db)."""
    raw = secrets.token_urlsafe(32)
    return raw, sha256_text(raw)
