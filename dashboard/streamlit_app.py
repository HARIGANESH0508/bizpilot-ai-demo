"""BizPilot AI - Enterprise finance operations dashboard (Streamlit).

Calls the same service layer as the FastAPI backend, so business rules are never duplicated in the UI.
Run from the project root:  streamlit run dashboard/streamlit_app.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import AuditLog, Discrepancy, GoodsReceipt, Invoice, PurchaseOrder, Supplier, User, WorkflowExecution  # noqa: E402
from app.security import can, verify_password  # noqa: E402
from app.services import analytics, approvals, audit, invoices, llm, matching  # noqa: E402
from app.services.agent import AgentSession, BusinessAgent  # noqa: E402
from app.services.errors import ServiceError  # noqa: E402
from app.services.seed import seed_demo  # noqa: E402

st.set_page_config(page_title="BizPilot AI", page_icon="🧭", layout="wide")

STATUS_COLORS = {"approved": "#22c55e", "awaiting_approval": "#3b82f6", "pending_review": "#94a3b8",
                 "flagged": "#f97316", "needs_information": "#eab308", "rejected": "#ef4444"}
SEVERITY_COLORS = {"low": "#38bdf8", "medium": "#eab308", "high": "#f97316", "critical": "#ef4444"}

CSS = """
<style>
  .stApp { background: linear-gradient(180deg,#0b1220 0%,#0f172a 100%); color:#e2e8f0; }
  section[data-testid="stSidebar"] { background:#0a1020; border-right:1px solid #1e293b; }
  div[data-testid="stMetric"] { background:#111b2e; border:1px solid #1e3a5f; border-radius:14px; padding:14px 18px;
      box-shadow:0 0 0 1px rgba(124,58,237,.08); }
  div[data-testid="stMetricLabel"] p { color:#94a3b8 !important; }
  div[data-testid="stMetricValue"] { color:#e0e7ff; }
  h1,h2,h3 { color:#e2e8f0 !important; letter-spacing:-0.01em; }
  .bp-badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; font-weight:600;
              background:#1e293b; color:#cbd5e1; border:1px solid #334155; }
  .bp-hint { color:#94a3b8; font-size:14px; }
  .stButton>button { border-radius:10px; }
</style>
"""


@st.cache_resource
def _bootstrap() -> bool:
    init_db()
    return True


def db():
    return SessionLocal()


def status_badge(status: str) -> str:
    return f'<span class="bp-badge" style="border-color:{STATUS_COLORS.get(status, "#334155")}">{status.replace("_", " ")}</span>'


def login_gate() -> None:
    st.title("🧭 BizPilot AI")
    st.caption("Enterprise invoice & procurement automation. Demo accounts: admin, fin.manager, ap.clerk, viewer.")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        with db() as session:
            user = session.scalar(select(User).where(User.username == username.strip()))
            if user and user.is_active and verify_password(password, user.password_hash):
                st.session_state.user = {"username": user.username, "role": user.role,
                                         "full_name": user.full_name}
                audit.record(session, user.username, "login", "user", user.id, {"channel": "dashboard"})
                session.commit()
                st.rerun()
            st.error("Invalid username or password.")


def page_overview() -> None:
    st.header("Executive Overview")
    with db() as session:
        s = analytics.summary(session)
        trend = analytics.monthly_trend(session)
        top = analytics.top_suppliers(session)
        by_status = s["by_status"]
    if s["total_invoices"] == 0:
        st.info("No invoices yet. Load demo data from System Admin or upload invoices in Invoice Intelligence.")
        return
    cols = st.columns(6)
    cols[0].metric("Total invoice value", f"AED {s['total_value']:,.2f}")
    cols[1].metric("Invoices", s["total_invoices"])
    cols[2].metric("Pending approvals", s["pending_approvals"])
    cols[3].metric("Flagged", s["flagged"])
    cols[4].metric("Approved", s["approved"], f"AED {s['approved_value']:,.0f}")
    cols[5].metric("Duplicate attempts", s["duplicate_attempts"])

    left, right = st.columns([2, 1])
    with left:
        if trend:
            df = pd.DataFrame(trend)
            fig = px.bar(df, x="month", y="value", title="Monthly invoice value (AED)",
                         color_discrete_sequence=["#7c3aed"], hover_data=["count"])
            fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
    with right:
        df = pd.DataFrame([{"status": k, "count": v} for k, v in by_status.items()])
        fig = px.pie(df, names="status", values="count", hole=0.55, title="Status mix",
                     color="status", color_discrete_map=STATUS_COLORS)
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
    if top:
        fig = px.bar(pd.DataFrame(top), x="value", y="supplier", orientation="h", title="Top suppliers by value",
                     color_discrete_sequence=["#3b82f6"])
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig, use_container_width=True)


def _invoice_options(session) -> dict[str, int]:
    rows = session.scalars(select(Invoice).order_by(Invoice.id.desc()).limit(300)).all()
    return {f"#{i.id} · {i.invoice_number or i.original_filename} · {i.status}": i.id for i in rows}


def page_intelligence(user: dict) -> None:
    st.header("Invoice Intelligence")
    st.markdown('<p class="bp-hint">Upload PDF, PNG or JPG invoices. Each file runs OCR, extraction, validation, '
                'matching and duplicate checks. Nothing is approved automatically.</p>', unsafe_allow_html=True)
    if can(user["role"], "upload"):
        files = st.file_uploader("Invoices", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
        if files and st.button("Process invoices", type="primary"):
            results = []
            with st.spinner("Running OCR, extraction and validation..."), db() as session:
                for f in files:
                    try:
                        results.append(invoices.process_upload(session, filename=f.name, data=f.getvalue(),
                                                               actor=user["username"]))
                    except ServiceError as exc:
                        results.append({"filename": f.name, "status": "rejected", "message": exc.message})
            st.dataframe(pd.DataFrame([{k: r.get(k) for k in ("filename", "status", "invoice_number", "risk_score",
                                                             "extraction_confidence", "message")}
                                       for r in results]), use_container_width=True, hide_index=True)
    else:
        st.info("Your role can view invoices but cannot upload them.")

    with db() as session:
        options = _invoice_options(session)
    if not options:
        st.caption("No invoices to review yet.")
        return
    choice = st.selectbox("Review an invoice", list(options.keys()))
    with db() as session:
        inv = invoices.get_invoice(session, options[choice])
        data = invoices.serialize_invoice(inv, detail=True)
        ocr_text = inv.ocr_text or ""
        st.markdown(status_badge(inv.status) + f"  &nbsp; Risk score **{inv.risk_score}**/100 &nbsp; "
                    f"Extraction indicator **{inv.extraction_confidence:.0%}**", unsafe_allow_html=True)
        st.progress(min(max(inv.extraction_confidence, 0.0), 1.0), text="Heuristic extraction indicator — review source fields")
        if data["missing_fields"]:
            st.warning("Missing required fields: " + ", ".join(data["missing_fields"]))

        c1, c2, c3 = st.columns(3)
        c1.write(f"**Supplier:** {data['supplier_name'] or '—'}")
        c1.write(f"**Tax ID:** {inv.supplier_tax_id or '—'}")
        c2.write(f"**Invoice date:** {data['invoice_date'] or '—'}")
        c2.write(f"**Due date:** {data['due_date'] or '—'}")
        c3.write(f"**PO reference:** {data['po_number'] or '—'}")
        c3.write(f"**Grand total:** {data['currency'] or ''} {data['grand_total']:,.2f}")

        if data["items"]:
            st.subheader("Line items")
            st.dataframe(pd.DataFrame(data["items"]), use_container_width=True, hide_index=True)
        st.subheader("Totals (recomputed in Python)")
        st.write(f"Subtotal {data['subtotal']:,.2f} · VAT {data['vat_rate']:g}% = {data['vat_amount']:,.2f} · "
                 f"Total {data['grand_total']:,.2f}")

        with st.expander("Extracted text (OCR)"):
            st.code(ocr_text[:4000] or "No text extracted.", language="text")
        with st.expander("Correct extracted fields and re-run checks"):
            if can(user["role"], "upload") and inv.status not in approvals.TERMINAL_STATUSES:
                with st.form(f"correct_{inv.id}"):
                    number = st.text_input("Invoice number", value=inv.invoice_number or "")
                    po_ref = st.text_input("PO number", value=inv.po_number_extracted or "")
                    supplier = st.text_input("Supplier name", value=inv.supplier_name or "")
                    if st.form_submit_button("Save corrections and re-validate"):
                        inv.invoice_number = number.strip() or None
                        inv.po_number_extracted = po_ref.strip() or None
                        inv.supplier_name = supplier.strip() or None
                        audit.record(session, user["username"], "extraction_corrected", "invoice", inv.id,
                                     {"invoice_number": inv.invoice_number, "po": inv.po_number_extracted})
                        session.commit()
                        invoices.reanalyze(session, inv, user["username"])
                        st.success("Corrections saved and invoice re-validated.")
                        st.rerun()
            else:
                st.caption("Corrections are not available for this invoice or role.")
    st.caption(f"Stored as {Path(inv.stored_path).name}. Files are not publicly served.")


def page_procurement() -> None:
    st.header("Procurement Management")
    with db() as session:
        pos = pd.DataFrame([{"PO": p.po_number, "Supplier": p.supplier.name, "Date": p.order_date,
                             "Currency": p.currency, "Lines": len(p.items),
                             "Value": sum(i.quantity * i.unit_price for i in p.items)}
                            for p in session.scalars(select(PurchaseOrder)).all()])
        suppliers = pd.DataFrame([{"Supplier": s.name, "Tax ID": s.tax_id, "Address": s.address}
                                  for s in session.scalars(select(Supplier)).all()])
        grns = pd.DataFrame([{"GRN": g.grn_number, "PO": g.po.po_number if g.po else None,
                              "Received": g.receipt_date,
                              "Lines": len(g.items), "Qty": sum(i.received_quantity for i in g.items)}
                             for g in session.scalars(select(GoodsReceipt)).all()])
    t1, t2, t3, t4 = st.tabs(["Purchase orders", "Suppliers", "Goods receipts", "Three-way matching"])
    with t1:
        st.dataframe(pos, use_container_width=True, hide_index=True) if not pos.empty else st.info("No purchase orders loaded.")
    with t2:
        st.dataframe(suppliers, use_container_width=True, hide_index=True) if not suppliers.empty else st.info("No suppliers loaded.")
    with t3:
        st.dataframe(grns, use_container_width=True, hide_index=True) if not grns.empty else st.info("No goods receipts loaded.")
    with t4:
        with db() as session:
            options = _invoice_options(session)
        if not options:
            st.info("Process an invoice first.")
            return
        choice = st.selectbox("Invoice to match", list(options.keys()), key="match_pick")
        with db() as session:
            inv = invoices.get_invoice(session, options[choice])
            po, issues = matching.match_invoice(session, inv)
        st.write(f"**Purchase order:** {po.po_number if po else 'not found'} · "
                 f"**Match risk:** {matching.risk_score(issues)}/100")
        if issues:
            st.dataframe(pd.DataFrame([i.__dict__ for i in issues]), use_container_width=True, hide_index=True)
        else:
            st.success("Invoice matches the purchase order and goods receipt.")


def page_exceptions() -> None:
    st.header("Exception Management")
    with db() as session:
        rows = session.execute(select(Discrepancy, Invoice.invoice_number, Invoice.id)
                               .join(Invoice, Discrepancy.invoice_id == Invoice.id)
                               .order_by(Discrepancy.id.desc())).all()
        data = [{"id": d.id, "invoice": n or f"#{iid}", "type": d.type, "severity": d.severity,
                 "message": d.message, "resolved": d.resolved, "note": d.resolution_note or ""}
                for d, n, iid in rows]
    if not data:
        st.success("No exceptions recorded.")
        return
    df = pd.DataFrame(data)
    open_df = df[~df["resolved"]]
    c1, c2, c3 = st.columns(3)
    c1.metric("Open exceptions", len(open_df))
    c2.metric("Critical or high (open)", int(open_df["severity"].isin(["critical", "high"]).sum()))
    c3.metric("Resolved", int(df["resolved"].sum()))
    fig = px.histogram(df, x="type", color="severity", title="Exceptions by type",
                       color_discrete_map=SEVERITY_COLORS)
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(df, use_container_width=True, hide_index=True)

    if open_df.empty:
        return
    with st.expander("Resolve an exception (finance manager or admin)"):
        user = st.session_state.user
        pick = st.selectbox("Open exception", open_df["id"].tolist(),
                            format_func=lambda i: f"#{i} · {open_df.set_index('id').loc[i, 'type']}")
        note = st.text_area("Resolution note (required)", key="res_note")
        if st.button("Mark resolved"):
            with db() as session:
                d = session.get(Discrepancy, int(pick))
                try:
                    approvals.resolve_discrepancy(session, d, user["username"], user["role"], note)
                    st.success("Exception resolved and recorded in the audit log.")
                    st.rerun()
                except ServiceError as exc:
                    st.error(exc.message)


def page_approvals(user: dict) -> None:
    st.header("Approval Center")
    st.caption("Approval records authorization only. BizPilot never triggers payments.")
    with db() as session:
        queue = session.scalars(select(Invoice).where(Invoice.status.in_(
            ["awaiting_approval", "flagged", "needs_information"])).order_by(Invoice.id)).all()
        options = {f"#{i.id} · {i.invoice_number or i.original_filename} · {i.status} · "
                   f"AED {i.grand_total:,.2f}": i.id for i in queue}
    if not options:
        st.success("The approval queue is empty.")
        return
    pick = st.selectbox("Invoice awaiting decision", list(options.keys()))
    with db() as session:
        inv = invoices.get_invoice(session, options[pick])
        blocking = approvals.open_blocking_discrepancies(inv)
        st.markdown(status_badge(inv.status) + f" &nbsp; Risk {inv.risk_score}/100", unsafe_allow_html=True)
        if blocking:
            st.error("Open high or critical exceptions: " + ", ".join(sorted({d.type for d in blocking})))
        for d in inv.discrepancies:
            if not d.resolved:
                st.write(f"- **{d.severity.upper()}** · {d.message}")
        st.caption(f"Approval threshold: AED {approvals.get_threshold(session):,.2f}")
        notes = st.text_area("Reviewer notes", key=f"notes_{inv.id}")
        b1, b2, b3 = st.columns(3)
        if b1.button("Approve", type="primary", disabled=not can(user["role"], "approve")):
            try:
                approvals.approve(session, inv, user["username"], user["role"], notes)
                st.success("Invoice approved. Recorded for finance execution; no payment was triggered.")
                st.rerun()
            except ServiceError as exc:
                st.error(exc.message)
        if b2.button("Reject", disabled=not can(user["role"], "reject")):
            try:
                approvals.reject(session, inv, user["username"], user["role"], notes)
                st.warning("Invoice rejected.")
                st.rerun()
            except ServiceError as exc:
                st.error(exc.message)
        if b3.button("Request information", disabled=not can(user["role"], "review")):
            try:
                approvals.request_information(session, inv, user["username"], user["role"], notes)
                st.info("Information requested.")
                st.rerun()
            except ServiceError as exc:
                st.error(exc.message)
        st.subheader("Decision history")
        hist = [{"decision": a.decision or "open", "by": a.decided_by or "-",
                 "at": a.decided_at.isoformat() if a.decided_at else "-", "notes": a.reviewer_notes}
                for a in inv.approvals]
        st.dataframe(pd.DataFrame(hist) if hist else pd.DataFrame(columns=["decision", "by", "at", "notes"]),
                     use_container_width=True, hide_index=True)
    if not can(user["role"], "approve"):
        st.caption("Your role can review but cannot approve. Sign in as a finance manager or admin.")


def page_ai_ops(user: dict) -> None:
    st.header("AI Operations Center")
    st.markdown('<p class="bp-hint">Ask in plain language. Answers come from database tools. Write actions need '
                'a confirmation code, for example <code>confirm a1b2c3</code>.</p>', unsafe_allow_html=True)
    if "agent_state" not in st.session_state:
        st.session_state.agent_state = AgentSession(actor=user["username"], role=user["role"])
        st.session_state.chat = []
    use_llm = st.toggle("Use local LLM for wording (Ollama)", value=False)
    for msg in st.session_state.chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    prompt = st.chat_input("e.g. 'show flagged invoices' or 'match invoice 2'")
    if prompt:
        st.session_state.chat.append({"role": "user", "content": prompt})
        with db() as session:
            agent = BusinessAgent(session, st.session_state.agent_state, use_llm=use_llm)
            reply = agent.handle(prompt)
        st.session_state.chat.append({"role": "assistant", "content": reply["answer"]})
        st.rerun()

    if st.button("Clear conversation"):
        st.session_state.chat = []
        st.session_state.agent_state = AgentSession(actor=user["username"], role=user["role"])
        st.rerun()

    st.subheader("Tool execution history")
    history = st.session_state.agent_state.history
    if history:
        st.dataframe(pd.DataFrame([{"at": h["at"], "tool": h["tool"], "ok": h["ok"], "confirmed": h["confirmed"],
                                    "error": h["error"] or ""} for h in history]),
                     use_container_width=True, hide_index=True)
    else:
        st.caption("No tools have run in this session.")
    with db() as session:
        errors = session.scalars(select(AuditLog).where(AuditLog.action == "agent_error")
                                 .order_by(AuditLog.id.desc()).limit(10)).all()
    st.subheader("Agent error monitoring")
    if errors:
        st.dataframe(pd.DataFrame([{"at": e.created_at, "tool": e.entity_id, "details": e.details} for e in errors]),
                     use_container_width=True, hide_index=True)
    else:
        st.success("No agent errors recorded.")


def page_audit() -> None:
    st.header("Audit & Analytics")
    with db() as session:
        report = analytics.full_report(session)
        rows = session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(300)).all()
        audit_df = pd.DataFrame([{"at": r.created_at, "actor": r.actor, "action": r.action,
                                  "entity": f"{r.entity_type}:{r.entity_id}", "details": r.details} for r in rows])
    proc = report["processing"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Avg processing time", f"{proc['avg_ms']} ms" if proc["avg_ms"] is not None else "—")
    c2.metric("Avg approval turnaround", f"{report['approval_turnaround_hours']} h"
              if report["approval_turnaround_hours"] is not None else "—")
    c3.metric("Audit events", len(audit_df))
    if report["exceptions"]:
        fig = px.bar(pd.DataFrame(report["exceptions"]), x="type", y="count", color="severity",
                     title="Exception statistics", color_discrete_map=SEVERITY_COLORS)
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
    action = st.multiselect("Filter by action", sorted(audit_df["action"].unique()) if not audit_df.empty else [])
    view = audit_df[audit_df["action"].isin(action)] if action else audit_df
    st.dataframe(view, use_container_width=True, hide_index=True)


def page_admin(user: dict) -> None:
    st.header("System Administration")
    health = {}
    with db() as session:
        try:
            session.execute(select(1))
            health["Database"] = {"ok": True, "detail": settings.database_url.split("///")[-1][:60]}
        except Exception as exc:
            health["Database"] = {"ok": False, "detail": str(exc)[:120]}
        wf = dict(session.execute(select(WorkflowExecution.status, func.count(WorkflowExecution.id))
                                  .group_by(WorkflowExecution.status)).all())
        threshold = approvals.get_threshold(session)
    try:
        r = httpx.get("http://127.0.0.1:8000/health", timeout=1.5)
        health["API"] = {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code} on :8000"}
    except httpx.HTTPError:
        health["API"] = {"ok": False, "detail": "Not reachable on :8000 (start uvicorn to enable)"}
    ollama = llm.ollama_health()
    health["Ollama"] = {"ok": ollama["ok"], "detail": f"model {settings.ollama_model}: "
                        f"{'ready' if ollama.get('model_ready') else 'not pulled'}" if ollama["ok"] else ollama.get("error", "")}
    try:
        from app.services.ocr import _pytesseract
        version = _pytesseract().get_tesseract_version()
        health["OCR (Tesseract)"] = {"ok": True, "detail": f"version {version}"}
    except Exception as exc:
        health["OCR (Tesseract)"] = {"ok": False, "detail": str(exc)[:120]}
    health["Workflow (n8n events)"] = {"ok": True, "detail": ", ".join(f"{k}: {v}" for k, v in wf.items()) or "no events yet"}

    cols = st.columns(len(health))
    for col, (name, info) in zip(cols, health.items()):
        col.metric(name, "Healthy" if info["ok"] else "Attention", info["detail"][:60], delta_color="normal" if info["ok"] else "inverse")

    st.subheader("Approval thresholds")
    st.write(f"Current threshold: **AED {threshold:,.2f}**. Invoices above it require an administrator to approve.")
    if can(user["role"], "admin"):
        with st.form("threshold"):
            new_value = st.number_input("New threshold (AED)", min_value=1.0, value=float(threshold), step=500.0)
            if st.form_submit_button("Save threshold"):
                with db() as session:
                    approvals.set_threshold(session, user["username"], user["role"], float(new_value))
                st.success("Threshold updated and logged.")
                st.rerun()
        st.subheader("Demo data")
        if st.button("Load demo data (idempotent)"):
            with st.spinner("Loading suppliers, POs, receipts and sample invoices..."), db() as session:
                res = seed_demo(session, actor=user["username"])
            st.json({k: v for k, v in res.items() if k != "invoices"})
    else:
        st.caption("Only administrators can change thresholds or load demo data.")


def main() -> None:
    _bootstrap()
    st.markdown(CSS, unsafe_allow_html=True)
    if "user" not in st.session_state:
        login_gate()
        return

    user = st.session_state.user
    pages = {
        "Executive Overview": lambda: page_overview(),
        "Invoice Intelligence": lambda: page_intelligence(user),
        "Procurement Management": page_procurement,
        "Exception Management": page_exceptions,
        "Approval Center": lambda: page_approvals(user),
        "AI Operations Center": lambda: page_ai_ops(user),
        "Audit & Analytics": page_audit,
        "System Administration": lambda: page_admin(user),
    }
    with st.sidebar:
        st.markdown("### 🧭 BizPilot AI")
        choice = st.radio("Navigate", list(pages.keys()), label_visibility="collapsed")
        st.markdown(f"**{user['full_name'] or user['username']}**  \n<span class='bp-hint'>{user['role']}</span>",
                    unsafe_allow_html=True)
        if st.button("Sign out"):
            del st.session_state["user"]
            st.rerun()
    pages[choice]()


main()
