"""Deterministic invoice arithmetic and consistency checks. Python is the source of truth, never the LLM."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.config import settings
from app.schemas import ExtractedInvoice, ValidationIssue

CENT = Decimal("0.01")
TOLERANCE = Decimal("0.02")


def _d(value) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def money(value) -> Decimal:
    return _d(value).quantize(CENT, rounding=ROUND_HALF_UP)


def compute_totals(items, discount_total=0.0, vat_rate: float | None = None) -> dict:
    """Recomputes every line and total independently of the extracted values."""
    rate = _d(vat_rate if vat_rate is not None else settings.default_vat_rate)
    line_totals = []
    for item in items:
        line = money(_d(item.quantity) * _d(item.unit_price) - _d(item.discount))
        line_totals.append(line)
    gross = sum(line_totals, Decimal("0.00"))
    net = money(gross - money(discount_total))
    vat = money(net * rate / Decimal("100"))
    return {
        "line_totals": [float(x) for x in line_totals],
        "subtotal": float(net),
        "vat_rate": float(rate),
        "vat_amount": float(vat),
        "grand_total": float(net + vat),
    }


def validate_invoice(extracted: ExtractedInvoice, today: date | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    today = today or date.today()

    for idx, item in enumerate(extracted.items, start=1):
        if item.line_total is not None:
            expected = money(_d(item.quantity) * _d(item.unit_price) - _d(item.discount))
            if abs(expected - money(item.line_total)) > TOLERANCE:
                issues.append(ValidationIssue(
                    type="line_total_mismatch", severity="high",
                    message=f"Line {idx} '{item.description}': stated {item.line_total:.2f}, "
                            f"quantity x price gives {expected:.2f}.",
                ))

    if extracted.items:
        totals = compute_totals(extracted.items, extracted.discount_total, extracted.vat_rate)
        if extracted.subtotal is not None and abs(_d(extracted.subtotal) - _d(totals["subtotal"])) > TOLERANCE:
            issues.append(ValidationIssue(
                type="subtotal_mismatch", severity="high",
                message=f"Stated subtotal {extracted.subtotal:.2f} differs from recomputed {totals['subtotal']:.2f}.",
            ))
        if extracted.vat_amount is not None and abs(_d(extracted.vat_amount) - _d(totals["vat_amount"])) > TOLERANCE:
            issues.append(ValidationIssue(
                type="tax_calculation_error", severity="high",
                message=f"VAT stated {extracted.vat_amount:.2f} but {totals['vat_rate']:g}% of "
                        f"{totals['subtotal']:.2f} is {totals['vat_amount']:.2f}.",
            ))
        if extracted.grand_total is not None and abs(_d(extracted.grand_total) - _d(totals["grand_total"])) > TOLERANCE:
            issues.append(ValidationIssue(
                type="total_mismatch", severity="high",
                message=f"Stated grand total {extracted.grand_total:.2f} differs from recomputed "
                        f"{totals['grand_total']:.2f}.",
            ))
        if extracted.vat_rate is not None and abs(extracted.vat_rate - settings.default_vat_rate) > 0.001:
            issues.append(ValidationIssue(
                type="unusual_vat_rate", severity="medium",
                message=f"VAT rate {extracted.vat_rate:g}% differs from the configured rate "
                        f"{settings.default_vat_rate:g}%.",
            ))

    if extracted.invoice_date and extracted.due_date and extracted.due_date < extracted.invoice_date:
        issues.append(ValidationIssue(
            type="date_inconsistency", severity="low", message="Due date is before the invoice date."))
    if extracted.invoice_date and extracted.invoice_date > today:
        issues.append(ValidationIssue(
            type="date_inconsistency", severity="low", message="Invoice date is in the future."))
    return issues
