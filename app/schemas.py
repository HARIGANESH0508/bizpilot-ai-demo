"""Pydantic models: structured extraction output, validation issues and API request bodies."""
from datetime import date

from pydantic import BaseModel, Field, field_validator


class ExtractedItem(BaseModel):
    product_code: str | None = None
    description: str
    quantity: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    discount: float = Field(default=0.0, ge=0)
    line_total: float | None = None


class ExtractedInvoice(BaseModel):
    supplier_name: str | None = None
    supplier_address: str | None = None
    supplier_tax_id: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    po_number: str | None = None
    items: list[ExtractedItem] = Field(default_factory=list)
    discount_total: float = 0.0
    subtotal: float | None = None
    vat_rate: float | None = None
    vat_amount: float | None = None
    grand_total: float | None = None
    field_confidence: dict[str, float] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    overall_confidence: float = 0.0
    ocr_method: str = ""

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("currency must be a 3-letter ISO code")
        return v


class ValidationIssue(BaseModel):
    type: str
    severity: str  # low | medium | high | critical
    message: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class DecisionRequest(BaseModel):
    notes: str = Field(default="", max_length=2000)


class ResolveRequest(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


class ThresholdRequest(BaseModel):
    value: float = Field(gt=0)
