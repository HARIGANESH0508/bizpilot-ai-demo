"""Upload validation and content-addressed storage. Files are never served publicly."""
import hashlib
from pathlib import Path

from app.config import settings
from app.services.errors import FileRejected

MAGIC_BYTES = {
    ".pdf": b"%PDF",
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
}


def validate_upload(filename: str, data: bytes) -> str:
    """Checks extension, size and magic bytes. Returns the normalized extension."""
    ext = Path(filename or "").suffix.lower()
    if ext not in MAGIC_BYTES:
        raise FileRejected(f"Unsupported file type '{ext or 'none'}'. Allowed: PDF, PNG, JPG.")
    if not data:
        raise FileRejected("Uploaded file is empty.")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise FileRejected(f"File exceeds the {settings.max_upload_mb} MB limit.")
    if not data.startswith(MAGIC_BYTES[ext]):
        raise FileRejected("File content does not match its extension.")
    return ext


def store_file(data: bytes, ext: str) -> tuple[str, str]:
    """Stores the file under its SHA-256 hash. Returns (sha256, absolute path)."""
    sha = hashlib.sha256(data).hexdigest()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    path = settings.upload_dir / f"{sha}{ext}"
    if not path.exists():
        path.write_bytes(data)
    return sha, str(path)
