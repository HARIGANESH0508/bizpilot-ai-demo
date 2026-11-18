"""Business Operations Agent.

Pipeline: message -> deterministic intent routing -> tool execution against the database -> templated answer.
The local LLM (optional) may only rephrase tool output. Write actions need an explicit 'confirm <code>' reply.
"""
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invoice
from app.services import audit, llm
from app.services.errors import ServiceError
from app.services.tools import REGISTRY, WRITE_TOOLS

# Order matters: first match wins. Patterns are checked against the lowercased message.
ROUTES: list[tuple[str, str]] = [
    (r"\bapprove\b", "approve_invoice"),
    (r"\b(reject|decline)\b", "reject_invoice"),
    (r"\b(approval request|request approval|create approval)\b", "create_approval_request"),
    (r"\bduplicate", "detect_duplicate_invoice"),
    (r"\b(line items?|compare)\b", "compare_invoice_line_items"),
    (r"\b(match|three[- ]way)\b", "match_purchase_order"),
    (r"\bvalidat|check (the )?(totals?|vat)", "validate_invoice"),
    (r"\b(extract|extracted|fields?)\b", "extract_invoice_data"),
    (r"\b(flagged|exceptions?|discrepanc\w*|risky)\b", "get_flagged_invoices"),
    (r"\b(pending|awaiting|to review)\b", "get_pending_invoices"),
    (r"\bapproved\b", "get_approved_invoices"),
    (r"\b(audit|history|log)\b", "get_audit_history"),
    (r"\b(report|trend|analytics)\b", "generate_financial_report"),
    (r"\bsupplier\b", "get_supplier_details"),
    (r"\b(purchase orders?|po-\w+)\b", "get_purchase_order_details"),
    (r"\b(summary|overview|dashboard|how many|total value)\b", "get_invoice_summary"),
    (r"\b(show|details?|open)\b", "extract_invoice_data"),
    (r"\b(search|find)\b", "search_invoice"),
]

INVOICE_REF = re.compile(r"(?:invoice\s*#?\s*|#)(\d+)\b", re.I)
INVOICE_NUMBER = re.compile(r"\b([A-Z]{2,5}-\d{4}-\d{3,6})\b", re.I)
PO_REF = re.compile(r"\b(PO-\d{4}-\d{3,6})\b", re.I)
CONFIRM = re.compile(r"^\s*confirm\s+([a-f0-9]{6})\s*$", re.I)


@dataclass
class AgentSession:
    actor: str
    role: str
    pending: dict[str, dict] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)


def _resolve_invoice_id(session: Session, text: str) -> int | None:
    m = INVOICE_REF.search(text)
    if m:
        return int(m.group(1))
    m = INVOICE_NUMBER.search(text)
    if m:
        inv = session.scalar(select(Invoice).where(Invoice.invoice_number == m.group(1).upper()))
        return inv.id if inv else None
    return None


def route(message: str) -> str | None:
    lowered = message.lower()
    for pattern, tool in ROUTES:
        if re.search(pattern, lowered):
            return tool
    return None


def _format_result(tool: str, data) -> str:
    if isinstance(data, list):
        if not data:
            return "No matching records were found in the database."
        lines = [f"Found {len(data)} record(s):"]
        for row in data[:20]:
            if isinstance(row, dict):
                label = row.get("invoice_number") or row.get("po_number") or row.get("supplier") or row.get("at")
                extra = row.get("status") or row.get("action") or ""
                value = f" | {row.get('currency') or ''} {row['grand_total']:,.2f}" if "grand_total" in row else ""
                lines.append(f"- {label}{value} {('| ' + extra) if extra else ''}".rstrip())
            else:
                lines.append(f"- {row}")
        return "\n".join(lines)
    if isinstance(data, dict):
        if "issues" in data and not data["issues"] and "valid" in data:
            return f"Invoice #{data['invoice_id']} passed all validation checks. No discrepancies found."
        if "issues" in data and isinstance(data["issues"], list):
            if not data["issues"]:
                return f"Invoice #{data['invoice_id']}: no discrepancies found."
            lines = [f"Invoice #{data.get('invoice_id')} has {len(data['issues'])} issue(s):"]
            lines += [f"- [{i['severity'].upper()}] {i['message']}" for i in data["issues"]]
            return "\n".join(lines)
        if "lines" in data:
            lines = [f"Line comparison for invoice #{data['invoice_id']} (PO: {data['purchase_order'] or 'none'}):"]
            for ln in data["lines"]:
                po = f"PO {ln['po_qty']:g} @ {ln['po_unit_price']:.2f}" if ln["matched_to_po"] else "not on PO"
                lines.append(f"- {ln['description']}: invoiced {ln['invoiced_qty']:g} @ "
                             f"{ln['invoiced_unit_price']:.2f} ({po})")
            return "\n".join(lines)
        return "\n".join(f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in data.items()
                         if not isinstance(v, (dict, list)))
    return str(data)


class BusinessAgent:
    def __init__(self, session: Session, agent_session: AgentSession, use_llm: bool = False):
        self.db = session
        self.state = agent_session
        self.use_llm = use_llm

    def handle(self, message: str) -> dict:
        message = (message or "").strip()
        if not message:
            return self._reply("Ask me about invoices, suppliers, purchase orders, approvals or reports.")

        confirm = CONFIRM.match(message)
        if confirm:
            return self._confirm(confirm.group(1).lower())

        tool = route(message)
        if tool is None:
            return self._reply(
                "I can help with invoice summaries, pending or flagged invoices, validation, PO matching, "
                "duplicate checks, approvals and audit history. Try: 'show flagged invoices' or 'match invoice 2'.")

        args, error = self._args_for(tool, message)
        if error:
            return self._reply(error, tool=tool)
        return self._execute(tool, args)

    def _args_for(self, tool: str, message: str) -> tuple[dict, str | None]:
        args: dict = {}
        needs_invoice = tool in {"extract_invoice_data", "validate_invoice", "match_purchase_order",
                                 "detect_duplicate_invoice", "compare_invoice_line_items",
                                 "create_approval_request", "approve_invoice", "reject_invoice"}
        if needs_invoice:
            inv_id = _resolve_invoice_id(self.db, message)
            if inv_id is None:
                return {}, "Which invoice? Give an id such as 'invoice 2' or a number such as 'INV-2026-001'."
            args["invoice_id"] = inv_id
        if tool in {"approve_invoice", "reject_invoice"}:
            args["notes"] = message[:500]
        if tool == "get_purchase_order_details":
            m = PO_REF.search(message)
            if not m:
                return {}, "Which purchase order? Include its number, for example PO-2026-0101."
            args["po_number"] = m.group(1).upper()
        if tool == "get_supplier_details":
            args["query"] = re.sub(r"(?i)\b(show|find|search|details?|of|for|the|supplier)\b", "", message).strip() or " "
        if tool == "search_invoice":
            args["query"] = re.sub(r"(?i)\b(search|find|show|invoice|invoices)\b", "", message).strip() or " "
        if tool == "get_audit_history":
            args["invoice_id"] = _resolve_invoice_id(self.db, message)
        return args, None

    def _execute(self, tool: str, args: dict) -> dict:
        if tool in WRITE_TOOLS:
            code = secrets.token_hex(3)
            self.state.pending[code] = {"tool": tool, "args": args, "created": datetime.utcnow().isoformat()}
            verb = tool.replace("_", " ")
            return self._reply(f"This will {verb} for invoice #{args['invoice_id']}. "
                               f"Reply 'confirm {code}' to proceed.", tool=tool, requires_confirmation=True,
                               token=code)
        return self._run(tool, args, confirmed=False)

    def _confirm(self, code: str) -> dict:
        pending = self.state.pending.pop(code, None)
        if pending is None:
            return self._reply(f"No pending action matches code {code}. It may have expired or already run.")
        return self._run(pending["tool"], pending["args"], confirmed=True)

    def _run(self, tool: str, args: dict, confirmed: bool) -> dict:
        fn = REGISTRY[tool]
        try:
            data = fn(self.db, actor=self.state.actor, role=self.state.role, **args)
            ok, error = True, None
        except ServiceError as exc:
            data, ok, error = None, False, exc.message
        except Exception as exc:  # agent must never crash the UI; the error is logged for monitoring
            self.db.rollback()
            data, ok, error = None, False, "Unexpected error while running the tool."
            audit.record(self.db, self.state.actor, "agent_error", "agent", tool,
                         {"error": str(exc)[:300]})
            self.db.commit()

        if tool not in WRITE_TOOLS:
            audit.record(self.db, self.state.actor, "agent_tool_executed", "agent", tool,
                         {"args": {k: v for k, v in args.items() if k != "notes"}, "ok": ok})
            self.db.commit()
        record = {"tool": tool, "args": args, "ok": ok, "error": error, "result": data,
                  "confirmed": confirmed, "at": datetime.utcnow().isoformat()}
        self.state.history.append(record)

        if not ok:
            return self._reply(error, tool=tool, data=None)
        answer = _format_result(tool, data)
        if self.use_llm:
            answer = llm.explain({"tool": tool, "result": data}, "Summarise this result for a finance user.") or answer
        return self._reply(answer, tool=tool, data=data)

    def _reply(self, answer: str, tool: str | None = None, data=None,
               requires_confirmation: bool = False, token: str | None = None) -> dict:
        return {"answer": answer, "tool": tool, "data": data,
                "requires_confirmation": requires_confirmation, "token": token}
