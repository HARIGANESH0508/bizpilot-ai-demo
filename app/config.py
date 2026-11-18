"""Central configuration. All values come from environment variables (or .env)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'bizpilot.db'}")
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "10"))
    approval_threshold: float = float(os.getenv("APPROVAL_THRESHOLD", "10000"))
    price_tolerance_pct: float = float(os.getenv("PRICE_TOLERANCE_PCT", "1.0"))
    default_vat_rate: float = float(os.getenv("DEFAULT_VAT_RATE", "5.0"))
    tesseract_cmd: str | None = os.getenv("TESSERACT_CMD") or None
    ollama_enabled: bool = _bool("OLLAMA_ENABLED", "true")
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    webhook_secret: str = os.getenv("N8N_WEBHOOK_SECRET", "")
    demo_password: str = os.getenv("DEMO_PASSWORD", "BizPilot#2026")
    pbkdf2_iterations: int = int(os.getenv("PBKDF2_ITERATIONS", "310000"))
    token_ttl_hours: int = 12


settings = Settings()
