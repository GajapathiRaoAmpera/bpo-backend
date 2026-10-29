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
| `GET /policies/{policy_no}` | as customers | 3 |
| `GET /dashboard/summary`, `/dashboard/trends` | SA, OM, TL, CM, QA, CO | 3 |
| `GET /calls`, `GET /calls/{id}` | OM, TL, QA, SA, CM, BD, CO; HA own only | 3 |
| ✅ `GET /voice/queue` (renewals due soonest) | TL, HA | 2 |
| ✅ `POST /voice/sessions` {mode, policy_id?} → call · `GET /voice/sessions/{id}` (transcript, customer, QA, flags) | TL, HA (owner; TL may read others) | 2 |
| ✅ `POST` / `PATCH /voice/sessions/{id}/offer` (WebRTC SDP offer / ICE candidates, Pipecat SmallWebRTC) | TL, HA (owner) | 2 |
| ✅ `POST /voice/sessions/{id}/takeover` · `POST /voice/sessions/{id}/end` {outcome?, note?} (outcome only after takeover) | TL, HA (owner) | 2 |
| Bot studio test calls on draft scripts | BD | 4 |
| `GET /live/calls` | TL, OM | 5 |
| `GET /escalations` · `POST /escalations/{id}/claim`, `/resolve` | read TL, HA, OM · act TL, HA | 3 |
| `GET /followups` · `POST /followups` · `PATCH /followups/{id}` | read TL, HA, CM, OM · write TL, HA, CM | 3 |
| `GET/POST /campaigns`, `PATCH` (draft), `/submit`, `/launch` | read CM, OM, CO · write CM | 4 |
| `POST /campaigns/{id}/approve`, `/reject` | OM, CO | 4 |
| `GET/POST /scripts`, `PATCH` (draft), `/submit`, `/test-call` | read BD, CO, OM · write BD | 4 |
| `POST /scripts/{id}/approve`, `/reject` | OM, CO | 4 |
| `GET /approvals` (pending + diff vs last approved) | OM, CO | 4 |
| `GET /qa/queue` · `PUT /qa/{call_id}` | read QA, OM, TL, CO · write QA | 4 |
| `GET /audit` · `GET /audit/export.csv` (audited) | SA, CO | 5 |
| `GET/POST/PATCH/DELETE /users` · `GET /integrations` | SA | 5 |
