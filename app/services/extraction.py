"""Structured field extraction. Deterministic line-anchored parsing first; the local LLM only fills gaps.

Every extracted value carries a confidence score. Missing required fields are reported explicitly.
"""
import re
from datetime import datetime

from app.schemas import ExtractedInvoice, ExtractedItem
from app.services import llm

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"]
REQUIRED_FIELDS = ["invoice_number", "supplier_name", "invoice_date", "currency", "grand_total", "items"]
NUM = r"([\d,]+(?:\.\d+)?)"
FLAGS = re.IGNORECASE | re.MULTILINE

PATTERNS = {
    "invoice_number": r"^[ \t]*invoice[ \t]*(?:no\.?|number|#)[ \t]*[:#][ \t]*([A-Z0-9][A-Z0-9\-/]{2,})",
    "po_number": r"^[ \t]*(?:PO|purchase[ \t]+order)[ \t]*(?:no\.?|number|#)?[ \t]*[:#][ \t]*([A-Z0-9][A-Z0-9\-/]{2,})",
    "supplier_name": r"^[ \t]*(?:supplier|from|seller)[ \t]*:[ \t]*(.+)$",
    "supplier_address": r"^[ \t]*(?:supplier[ \t]+)?address[ \t]*:[ \t]*(.+)$",
    "supplier_tax_id": r"^[ \t]*(?:tax|vat)[ \t]*(?:reg(?:istration)?\.?[ \t]*)?(?:no\.?|number|id)[ \t]*:[ \t]*([0-9A-Z]{8,20})",
    "invoice_date": r"^[ \t]*invoice[ \t]+date[ \t]*:[ \t]*(.+)$",
    "due_date": r"^[ \t]*due[ \t]+date[ \t]*:[ \t]*(.+)$",
    "currency": r"^[ \t]*currency[ \t]*:[ \t]*([A-Za-z]{3})\b",
    "discount_total": r"^[ \t]*discount[ \t]*:[ \t]*" + NUM,
    "subtotal": r"^[ \t]*sub[ \t-]*total[ \t]*:[ \t]*" + NUM,
    "vat_amount": r"^[ \t]*vat(?:[ \t]*\([^)]*\))?[ \t]*:[ \t]*" + NUM,
    "vat_rate": r"^[ \t]*vat[ \t]*\([ \t]*([\d.]+)[ \t]*%[ \t]*\)",
    "grand_total": r"^[ \t]*(?:grand[ \t]+total|total[ \t]*due|total[ \t]*amount|total)[ \t]*:[ \t]*" + NUM,
}


def _to_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None


def parse_date(text: str | None):
    if not text:
        return None
    candidate = text.strip().rstrip(".")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def _first(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, FLAGS)
    return m.group(1).strip() if m else None


def _parse_items(lines: list[str]) -> list[ExtractedItem]:
    """Reads the table between a 'Description | Qty' header and the first totals line.

    Accepted row shapes (cells split on '|', tabs or 2+ spaces):
        CODE | DESCRIPTION | QTY | UNIT PRICE | LINE TOTAL
        DESCRIPTION | QTY | UNIT PRICE | LINE TOTAL
    """
    items: list[ExtractedItem] = []
    in_table = False
    for line in lines:
        low = line.strip().lower()
        if not in_table:
            if re.search(r"\bdescription\b", low) and re.search(r"\b(qty|quantity)\b", low):
                in_table = True
            continue
        if re.match(r"(sub[ \t-]*total|discount|vat|grand|total)\b", low):
            break
        cells = [c.strip() for c in re.split(r"\s*\|\s*|\t|\s{2,}", line.strip()) if c.strip()]
        if len(cells) < 4:
            continue
        qty, unit, total = _to_float(cells[-3]), _to_float(cells[-2]), _to_float(cells[-1])
        if qty is None or unit is None or total is None or qty <= 0:
            continue
        leading = cells[:-3]
        if len(leading) >= 2:
            code, desc = leading[0], " ".join(leading[1:])
        else:
            code, desc = None, leading[0]
        items.append(ExtractedItem(product_code=code, description=desc, quantity=qty,
                                   unit_price=unit, line_total=total))
    return items


def extract_invoice(text: str, ocr_method: str = "") -> ExtractedInvoice:
    lines = text.splitlines()
    confidence: dict[str, float] = {}
    values: dict[str, str | None] = {}

    for key, pattern in PATTERNS.items():
        raw = _first(pattern, text)
        values[key] = raw
        if raw is not None:
            confidence[key] = 0.95

    if values["supplier_name"] is None:
        first_line = next((ln.strip() for ln in lines if ln.strip()), None)
        if first_line and not re.search(r"invoice", first_line, re.I):
            values["supplier_name"] = first_line
            confidence["supplier_name"] = 0.6

    items = _parse_items(lines)
    if items:
        confidence["items"] = 0.9

    currency = values["currency"].upper() if values["currency"] else None
    extracted = ExtractedInvoice(
        supplier_name=values["supplier_name"],
        supplier_address=values["supplier_address"],
        supplier_tax_id=values["supplier_tax_id"],
        invoice_number=values["invoice_number"],
        invoice_date=parse_date(values["invoice_date"]),
        due_date=parse_date(values["due_date"]),
        currency=currency,
        po_number=values["po_number"],
        items=items,
        discount_total=_to_float(values["discount_total"]) or 0.0,
        subtotal=_to_float(values["subtotal"]),
        vat_rate=_to_float(values["vat_rate"]),
        vat_amount=_to_float(values["vat_amount"]),
        grand_total=_to_float(values["grand_total"]),
        ocr_method=ocr_method,
    )
    if extracted.invoice_date is not None and values["invoice_date"]:
        confidence["invoice_date"] = 0.95
    elif values["invoice_date"]:
        confidence["invoice_date"] = 0.0  # present but unreadable

    missing = _missing(extracted)
    if missing:
        suggestions = llm.suggest_missing_fields(text, [m for m in missing if m != "items"])
        extracted = _apply_suggestions(extracted, suggestions, confidence)
        missing = _missing(extracted)

    extracted.field_confidence = confidence
    extracted.missing_fields = missing
    extracted.overall_confidence = _overall(confidence, missing)
    return extracted


def _missing(e: ExtractedInvoice) -> list[str]:
    checks = {
        "invoice_number": bool(e.invoice_number),
        "supplier_name": bool(e.supplier_name),
        "invoice_date": e.invoice_date is not None,
        "currency": bool(e.currency),
        "grand_total": e.grand_total is not None,
        "items": bool(e.items),
    }
    return [name for name in REQUIRED_FIELDS if not checks[name]]


def _overall(confidence: dict[str, float], missing: list[str]) -> float:
    scores = [0.0 if f in missing else confidence.get(f, 0.9) for f in REQUIRED_FIELDS]
    return round(sum(scores) / len(scores), 3)


def _apply_suggestions(e: ExtractedInvoice, suggestions: dict, confidence: dict[str, float]) -> ExtractedInvoice:
    """Accepts LLM suggestions only when they validate, and marks them low confidence."""
    update: dict = {}
    if isinstance(suggestions.get("invoice_number"), str) and not e.invoice_number:
        update["invoice_number"] = suggestions["invoice_number"].strip()
    if isinstance(suggestions.get("supplier_name"), str) and not e.supplier_name:
        update["supplier_name"] = suggestions["supplier_name"].strip()
    if isinstance(suggestions.get("currency"), str) and not e.currency:
        if re.fullmatch(r"[A-Za-z]{3}", suggestions["currency"].strip()):
            update["currency"] = suggestions["currency"].strip().upper()
    if isinstance(suggestions.get("grand_total"), (int, float)) and e.grand_total is None:
        update["grand_total"] = float(suggestions["grand_total"])
    if isinstance(suggestions.get("invoice_date"), str) and e.invoice_date is None:
        parsed = parse_date(suggestions["invoice_date"])
        if parsed:
            update["invoice_date"] = parsed
    for key in update:
        confidence[key] = 0.5  # LLM-suggested: must be reviewed
    return e.model_copy(update=update) if update else e
