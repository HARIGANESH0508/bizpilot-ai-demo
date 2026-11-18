"""SQLAlchemy ORM models for the finance-operations domain."""
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(32))  # admin | finance_manager | ap_clerk | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(default=utcnow)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    tax_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    address: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    po_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    order_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="AED")
    status: Mapped[str] = mapped_column(String(20), default="open")
    supplier: Mapped[Supplier] = relationship()
    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        back_populates="po", cascade="all, delete-orphan", order_by="PurchaseOrderItem.line_no"
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[float] = mapped_column(Float)
    unit_price: Mapped[float] = mapped_column(Float)
    po: Mapped[PurchaseOrder] = relationship(back_populates="items")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grn_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    receipt_date: Mapped[date] = mapped_column(Date)
    po: Mapped[PurchaseOrder] = relationship()
    items: Mapped[list["GoodsReceiptItem"]] = relationship(
        back_populates="grn", cascade="all, delete-orphan"
    )


class GoodsReceiptItem(Base):
    __tablename__ = "goods_receipt_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grn_id: Mapped[int] = mapped_column(ForeignKey("goods_receipts.id", ondelete="CASCADE"), index=True)
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    received_quantity: Mapped[float] = mapped_column(Float)
    grn: Mapped[GoodsReceipt] = relationship(back_populates="items")


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True, index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    supplier_tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supplier_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True, index=True)
    po_number_extracted: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    discount_total: Mapped[float] = mapped_column(Float, default=0.0)
    vat_rate: Mapped[float] = mapped_column(Float, default=0.0)
    vat_amount: Mapped[float] = mapped_column(Float, default=0.0)
    grand_total: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="pending_review", index=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    missing_fields: Mapped[str] = mapped_column(Text, default="[]")
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    ocr_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.line_no"
    )
    discrepancies: Mapped[list["Discrepancy"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="Discrepancy.id"
    )
    approvals: Mapped[list["ApprovalRequest"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="ApprovalRequest.id"
    )


Index("ix_invoices_supplier_number", Invoice.supplier_name, Invoice.invoice_number)


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[float] = mapped_column(Float)
    unit_price: Mapped[float] = mapped_column(Float)
    discount: Mapped[float] = mapped_column(Float, default=0.0)
    line_total: Mapped[float] = mapped_column(Float, default=0.0)
    invoice: Mapped[Invoice] = relationship(back_populates="items")


class Discrepancy(Base):
    __tablename__ = "discrepancies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))  # low | medium | high | critical
    message: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    invoice: Mapped[Invoice] = relationship(back_populates="discrepancies")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    requested_by: Mapped[str] = mapped_column(String(64), default="system")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)  # approved | rejected | info_requested
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reviewer_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    invoice: Mapped[Invoice] = relationship(back_populates="approvals")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[str] = mapped_column(Text, default="{}")


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)  # duplicate-event protection
    workflow_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))  # received | completed | failed
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
