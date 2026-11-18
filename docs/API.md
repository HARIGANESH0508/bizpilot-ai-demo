# API reference

Base URL: `http://localhost:8000`. Interactive docs: `/docs`. OpenAPI JSON: `/openapi.json`.

## Authentication

```powershell
$body = @{ username = "fin.manager"; password = "BizPilot#2026" } | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/auth/login -Body $body -ContentType "application/json"
$headers = @{ Authorization = "Bearer $($login.access_token)" }
```

Every endpoint except `/health` requires `Authorization: Bearer <token>`. Tokens expire after 12 hours.

## Error format

All errors use one shape:

```json
{ "error": { "code": "forbidden", "message": "Role 'viewer' lacks the 'upload' permission." } }
```

| HTTP | Code | Meaning |
|---|---|---|
| 401 | `unauthorized` | Missing, invalid or expired token; bad credentials |
| 403 | `forbidden` | Role lacks the permission, or amount is above the threshold |
| 404 | `not_found` | Unknown invoice, PO, supplier or discrepancy |
| 409 | `invalid_state` | Transition not allowed from the current status |
| 422 | `invalid_file` / `validation_error` | Bad input |
| 500 | `internal_error` | Unexpected error (details logged, not returned) |

## Endpoints

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/health` | none | Liveness and database check |
| GET | `/health/details` | admin | Ollama, Tesseract, upload directory |
| POST | `/api/auth/login` | none | Returns bearer token |
| POST | `/api/invoices/upload` | upload | Multipart `files`; processes each file, returns per-file result |
| GET | `/api/invoices?status=&limit=` | read | List invoices |
| GET | `/api/invoices/{id}` | read | Invoice with items, discrepancies and approval history |
| POST | `/api/invoices/{id}/validate` | upload | Re-runs the full pipeline and saves the result |
| POST | `/api/invoices/{id}/match` | read | Read-only three-way match |
| POST | `/api/invoices/{id}/approve` | approve | Body `{"notes": "..."}`. Blocked by open high or critical exceptions and by the threshold |
| POST | `/api/invoices/{id}/reject` | reject | Body `{"notes": "..."}` |
| POST | `/api/invoices/{id}/request-info` | review | Body `{"notes": "..."}` |
| POST | `/api/discrepancies/{id}/resolve` | resolve_discrepancy | Body `{"note": "..."}` (3+ characters) |
| GET | `/api/purchase-orders` | read | Purchase order summary |
| GET | `/api/suppliers` | read | Supplier master |
| GET | `/api/audit?limit=&invoice_id=` | read | Audit events, newest first |
| GET | `/api/analytics` | read | KPIs, monthly trend, top suppliers, turnaround, performance, exceptions |
| POST | `/api/webhooks/n8n/invoice-event` | shared secret | Runs the pipeline for an invoice from n8n |

### Upload example (PowerShell 7+)

```powershell
$form = @{ files = Get-Item .\data\samples\invoices\INV-2026-002_price_mismatch.pdf }
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/invoices/upload -Headers $headers -Form $form
```

Response (abridged):

```json
{ "results": [ { "invoice_id": 3, "invoice_number": "INV-2026-002", "status": "flagged", "risk_score": 30,
  "discrepancies": [ { "type": "price_mismatch", "severity": "high", "message": "Line 1 ..." } ] } ] }
```

Possible per-file `status` values: `awaiting_approval`, `flagged`, `needs_information`, `duplicate`, `rejected`.

## n8n integration

Import `n8n/bizpilot_invoice_workflow.json` into n8n. Set `N8N_WEBHOOK_SECRET` in n8n to the same value as in BizPilot's `.env`.

Flow: webhook receives `{ "event_id": "...", "invoice_id": 3 }` → HTTP node calls `/api/webhooks/n8n/invoice-event` with `X-Event-Id` and `X-Webhook-Secret` → IF node routes on `status` → notification placeholder or error alert → acknowledgement.

Reliability:
- **Retries:** the HTTP node retries 3 times, 2 seconds apart.
- **Duplicate events:** a repeated `X-Event-Id` returns `{"status": "duplicate_event"}` and does not run the pipeline twice.
- **Failures:** a failed run is recorded in `workflow_executions` with its error, and the same event id can be retried.
- **Never pays:** the workflow only triggers analysis and notification.

Status: the JSON is valid and the endpoint is tested. The workflow has not yet been imported into a live n8n instance.
