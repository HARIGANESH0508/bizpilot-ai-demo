# Demo scenario (about 6 minutes)

Start the API and dashboard, then sign in as **`admin`**. Run `python scripts/seed_demo.py` first if you have not already.

## 1. Executive overview (1 min)
Show the KPI cards: invoice value, pending approvals, flagged count, duplicate attempts. Point out the monthly trend and top suppliers.

## 2. Invoice intelligence (2 min)
Open **INV-2026-001** (awaiting approval). Show:
- Extracted header fields and line items with codes
- Totals recomputed in Python: AED 13,300.00 subtotal, 665.00 VAT, 13,965.00 total
- Confidence and an empty missing-fields list

Then open **INV-2026-005**. Stated VAT is 40.00, but 5% of 900.00 is 45.00. The system recomputes the total and flags the error. Point out that no LLM was involved in that calculation.

## 3. Three-way matching (1 min)
Procurement tab → Three-way matching → **INV-2026-002**. The printer is invoiced at AED 1,550 against a PO price of AED 1,400, a 10.7% increase. It is flagged high.

## 4. Duplicates (30 sec)
Open **INV-2026-001 (resent)**. The same supplier re-sent invoice number INV-2026-001 with a different file. It is flagged as a supplier invoice-number duplicate, not blocked, because only an identical file is refused automatically.

## 5. Approval controls (1 min)
Approval Center → **INV-2026-001 clean** (AED 13,965, above the AED 10,000 demo threshold). Only an admin can approve it. Explain the threshold and that approval records authorization without triggering a payment.

Sign out, sign in as **`fin.manager`**, and show that flagged invoices cannot be approved until the exception is resolved.

## 6. AI operations (1 min)
AI Operations Center. Try:
- `show flagged invoices`
- `match invoice 3`
- `approve invoice 1` → the agent returns a confirmation code. Explain that nothing happens until the user types `confirm <code>`. Then show the tool execution history.

## 7. Audit and analytics (30 sec)
Audit tab: filter for `invoice_approved` and `duplicate_detected`. Point out that every decision has an actor and a timestamp.

## Talking points
- Deterministic arithmetic and matching; the LLM is optional and never produces the numbers.
- Role-based approvals with a configurable threshold, enforced identically in the API, dashboard and agent.
- Free stack: Tesseract, SQLite, Ollama, n8n Community Edition.
- Known limitation: the parser is tuned to labelled invoices. New layouts need templates or the LLM assist.
