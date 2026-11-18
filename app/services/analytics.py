"""Read-only analytics over the database. Charts are rendered by the dashboard with Plotly."""
from collections import Counter, defaultdict
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ApprovalRequest, AuditLog, Discrepancy, Invoice

OPEN_STATUSES = ["pending_review", "awaiting_approval", "flagged", "needs_information"]


def summary(session: Session) -> dict:
    invoices = session.scalars(select(Invoice)).all()
    by_status = Counter(i.status for i in invoices)
    approved = [i for i in invoices if i.status == "approved"]
    duplicate_attempts = session.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.action.in_(["duplicate_upload_blocked", "duplicate_detected"]))) or 0
    return {
        "total_invoices": len(invoices),
        "total_value": round(sum(i.grand_total for i in invoices), 2),
        "pending_approvals": by_status.get("awaiting_approval", 0),
        "flagged": by_status.get("flagged", 0),
        "needs_information": by_status.get("needs_information", 0),
        "approved": len(approved),
        "approved_value": round(sum(i.grand_total for i in approved), 2),
        "rejected": by_status.get("rejected", 0),
        "duplicate_attempts": duplicate_attempts,
        "by_status": dict(by_status),
    }


def monthly_trend(session: Session) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"count": 0, "value": 0.0})
    for inv in session.scalars(select(Invoice)).all():
        when = inv.invoice_date or inv.created_at.date()
        key = when.strftime("%Y-%m")
        buckets[key]["count"] += 1
        buckets[key]["value"] += inv.grand_total
    return [{"month": k, "count": v["count"], "value": round(v["value"], 2)} for k, v in sorted(buckets.items())]


def top_suppliers(session: Session, limit: int = 5) -> list[dict]:
    totals: dict[str, float] = defaultdict(float)
    for inv in session.scalars(select(Invoice)).all():
        totals[inv.supplier_name or "Unknown"] += inv.grand_total
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"supplier": k, "value": round(v, 2)} for k, v in ranked]


def turnaround_hours(session: Session) -> float | None:
    rows = session.scalars(select(ApprovalRequest).where(ApprovalRequest.decided_at.is_not(None))).all()
    if not rows:
        return None
    hours = [(r.decided_at - r.created_at).total_seconds() / 3600 for r in rows]
    return round(sum(hours) / len(hours), 2)


def processing_performance(session: Session) -> dict:
    times = []
    for row in session.scalars(select(AuditLog).where(AuditLog.action == "invoice_analyzed")).all():
        ms = json.loads(row.details or "{}").get("processing_ms")
        if isinstance(ms, (int, float)):
            times.append(ms)
    if not times:
        return {"runs": 0, "avg_ms": None, "max_ms": None}
    return {"runs": len(times), "avg_ms": round(sum(times) / len(times), 1), "max_ms": max(times)}


def exception_stats(session: Session) -> list[dict]:
    rows = session.execute(select(Discrepancy.type, Discrepancy.severity, func.count(Discrepancy.id))
                           .group_by(Discrepancy.type, Discrepancy.severity)).all()
    return [{"type": t, "severity": s, "count": c} for t, s, c in rows]


def full_report(session: Session) -> dict:
    return {
        "summary": summary(session),
        "monthly_trend": monthly_trend(session),
        "top_suppliers": top_suppliers(session),
        "approval_turnaround_hours": turnaround_hours(session),
        "processing": processing_performance(session),
        "exceptions": exception_stats(session),
    }
