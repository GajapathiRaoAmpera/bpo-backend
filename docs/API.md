# API

The machine-readable contract is `/openapi.json` (the frontend generates its types from it). This file lists conventions and the planned surface with roles.

## Conventions
- Base: `/api/v1`. Health: `GET /health`.
- Auth: httpOnly cookie `av_session` (set by `POST /auth/login`).
- Errors: `{"detail": "<message>"}`; 401 not signed in, 403 role not allowed, 404 not found, 422 validation.
- Pagination: `?page=1&page_size=25` (max 100) → `{items, total, page, page_size}`.
- Times: ISO 8601 UTC in JSON; the UI shows IST.

## Endpoints
Role codes: SA super_admin, OM ops_manager, TL team_lead, HA human_agent, CM campaign_manager, BD bot_designer, QA qa_analyst, CO compliance_officer. ✅ = implemented.

| Endpoint | Roles | Phase |
|---|---|---|
| ✅ `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` (user, role label, landing, menu) | public / any signed-in | 1 |
| ✅ `GET /customers` (search q, paginated) · `GET /customers/{id}` (audited) | TL, HA full; OM, CM, QA, CO masked | 1 |
| ✅ `GET /rbac/matrix` | SA | 1 |
| ✅ `GET /policies/{policy_no}` (policy + customer, masked-aware, audited) | as customers | 3 |
| ✅ `GET /dashboard/summary`, `/dashboard/trends` (?days=1–90; calls, renewals paid, collections ₹, containment, QA, sentiment per IST day) | SA, OM, TL, CM, QA, CO | 3 |
| ✅ `GET /calls` (q, outcome, sentiment, mode, date_from/to, paginated) · `GET /calls/{id}` (transcript, QA, escalations, follow-ups; audited; customer masked unless full Customer 360 access; others' calls 404 for HA) | OM, TL, QA, SA, CM, BD, CO; HA own only | 3 |
| ✅ `GET /voice/queue` (renewals due soonest) | TL, HA | 2 |
| ✅ `POST /voice/sessions` {mode, policy_id?} → call · `GET /voice/sessions/{id}` (transcript, customer, QA, flags) | TL, HA (owner; TL may read others) | 2 |
| ✅ `POST` / `PATCH /voice/sessions/{id}/offer` (WebRTC SDP offer / ICE candidates, Pipecat SmallWebRTC) | TL, HA (owner) | 2 |
| ✅ `POST /voice/sessions/{id}/takeover` · `POST /voice/sessions/{id}/end` {outcome?, note?} (outcome only after takeover) | TL, HA (owner) | 2 |
| ✅ `POST /scripts/{id}/test-call` (simulated text preview + keyword compliance pre-check; no model call, nothing saved) | BD | 4 |
| ✅ `GET /live/calls` (in-progress calls; customer phone masked and customer speech hidden for OM) | TL, OM | 5 |
| ✅ `GET /escalations` (active queue by priority, or ?status=) · `POST /escalations/{id}/claim`, `/resolve` {note} (audited; 409 if claimed by someone else, TL may resolve any) | read TL, HA, OM · act TL, HA | 3 |
| ✅ `GET /followups` (?status, kind, paginated) · `POST /followups` · `PATCH /followups/{id}` (done/cancel, reschedule; audited) | read TL, HA, CM, OM · write TL, HA, CM | 3 |
| ✅ `GET/POST /campaigns`, `GET /campaigns/audience` (wizard preview), `PATCH` (draft), `/submit`, `/launch` (approved/running; 3 calls per launch: real Plivo calls when `PLIVO_*` + `PUBLIC_BASE_URL` are set, only to `CONTACT_ALLOWLIST_PREFIX` customers if set; otherwise simulated in sandbox and 409 in production) | read CM, OM, CO · write CM | 4 |
| ✅ `POST /telephony/plivo/answer/{call_id}` (Plivo XML `<Stream>` to the WebSocket) · `WS /telephony/plivo/ws/{call_id}/{token}` (call audio ↔ bot) · `POST /telephony/plivo/hangup/{call_id}` (closes the call) | Plivo only: `X-Plivo-Signature-V3` on webhooks, one-time token on the WebSocket; no user session | 6b |
| ✅ `POST /campaigns/{id}/approve`, `/reject` {checklist, note} (all checks to approve; note to reject; not on own items) | OM, CO | 4 |
| ✅ `GET/POST /scripts`, `PATCH` (draft), `/submit` (new version = new row with parent_id) | read BD, CO (OM reads pending scripts via `/approvals`; no `bot_studio` access in the matrix) · write BD | 4 |
| ✅ `POST /scripts/{id}/approve`, `/reject` {checklist, note} | OM, CO | 4 |
| ✅ `GET /approvals` (pending scripts + campaigns, side-by-side line diff vs the active script) | OM, CO | 4 |
| ✅ `GET /qa/queue` (?state=open\|locked\|all) · `GET /qa/{call_id}` (transcript, DOB redacted) · `PUT /qa/{call_id}` {5 checks, note, lock} (locked → 409) | read QA, OM, TL, CO · write QA | 4 |
| ✅ `GET /audit` (paginated; filters actor name/email, action, entity_type, date_from/date_to IST) · `GET /audit/export.csv` (same filters, streamed, audited) | SA, CO | 5 |
| ✅ `GET/POST/PATCH/DELETE /users` (DELETE deactivates; no self-deactivate or self role change) · `GET /integrations` | SA | 5 |
