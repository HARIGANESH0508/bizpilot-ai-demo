"""Optional local LLM via Ollama (free, runs on your machine). Used only for suggestions and wording.

Rules: LLM output is never trusted as a fact. Field values it suggests are marked low-confidence,
and totals and business decisions are always computed in Python.
"""
import json

import httpx

from app.config import settings


def ollama_health() -> dict:
    try:
        resp = httpx.get(f"{settings.ollama_url}/api/tags", timeout=2.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        return {"ok": True, "models": models, "model_ready": settings.ollama_model in models}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:200], "models": [], "model_ready": False}


def _generate(prompt: str, json_mode: bool) -> str | None:
    if not settings.ollama_enabled:
        return None
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    if json_mode:
        payload["format"] = "json"
    try:
        resp = httpx.post(f"{settings.ollama_url}/api/generate", json=payload, timeout=60.0)
        resp.raise_for_status()
        return resp.json().get("response", "")
    except (httpx.HTTPError, ValueError):
        return None


def suggest_missing_fields(text: str, missing: list[str]) -> dict:
    """Asks the local model for fields the parser could not find. Returns {} when unavailable."""
    if not missing:
        return {}
    prompt = (
        "You extract fields from an invoice. Return only a JSON object with these keys: "
        f"{', '.join(missing)}. Use null when a value is not present. Do not guess.\n\nINVOICE TEXT:\n"
        f"{text[:6000]}"
    )
    raw = _generate(prompt, json_mode=True)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def explain(facts: dict, question: str) -> str | None:
    """Turns tool results into a short explanation. Facts must come from the database, not the model."""
    prompt = (
        "Answer the question using ONLY the facts below. If the facts are insufficient, say so. "
        "Keep it to 2-3 sentences.\n\n"
        f"QUESTION: {question}\nFACTS (JSON): {json.dumps(facts, default=str)[:4000]}"
    )
    text = _generate(prompt, json_mode=False)
    return text.strip() if text else None
