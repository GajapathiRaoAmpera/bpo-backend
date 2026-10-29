# Architecture (backend)

## 1. System Overview
FastAPI service that owns all data, business rules, RBAC, masking and audit for AssureVoice AI, plus (from Phase 2) the Pipecat voice agent. The Next.js frontend (`bpo-frontend`) is a separate repo that talks to it only through the OpenAPI contract.

## 2. Pattern
Modular monolith. One process, layered packages. Reason: a sandbox showcase with one team; the voice agent shares the DB and tools with the API.

## 3. Technology Stack
| Concern | Choice |
|---|---|
| Runtime | Python 3.12 (>=3.11), Poetry |
| API | FastAPI, Pydantic v2 |
| DB | SQLAlchemy 2.0; SQLite now, PostgreSQL / Cloud SQL by changing `DATABASE_URL` |
| Auth | PyJWT (HS256) in an httpOnly cookie, passlib[bcrypt] |
| Scheduling | APScheduler (Phase 3) |
| AI | google-genai: Gemini Live (voice), Gemini Flash (summaries, QA, messages) |
| Voice | Pipecat with WebRTC transport (Phase 2) |

## 4. Components
```
Browser ──> Next.js (rewrites /api/*) ──> FastAPI /api/v1 ──> service layer ──> SQLAlchemy ──> SQLite/Postgres
Browser ──(WebRTC, Pipecat JS client)──> Pipecat bot (app/voice) ──> Gemini Live
                                                    └── tools ──> service layer ──> DB
```

## 5. Project Structure
```
app/
  api/v1/      routers: thin; parse input, call services, return schemas
  core/        config, db, security (JWT/bcrypt), rbac (access matrix), masking, audit, formatting
  models/      SQLAlchemy models (__init__.py) + enums.py
  schemas/     Pydantic request/response models (the OpenAPI contract)
  services/    business rules (e.g. scripts.active_script)
  voice/       Pipecat bot and tools (Phase 2)
  seed/        deterministic synthetic data; `python -m app.seed`
tests/         pytest
```

## 6. Request Flow
1. Next.js forwards `/api/v1/...` with the session cookie.
2. `current_user` decodes the JWT, loads the user from the DB (inactive → 401).
3. `require(Screen.x, *levels)` checks `core/rbac.MATRIX` → 403 if not allowed.
4. Router calls the service / query; masking is applied when building the response schema.
5. Explicit `audit.record(...)` for logins, customer views, approvals, exports; the audit middleware records any other successful authenticated write.

## 7. Dependency Rules
- Routers never contain business rules beyond simple queries; shared rules live in `services/`.
- The access matrix exists only in `core/rbac.py`. The UI menu (`/auth/me`) and permission-matrix screen (`/rbac/matrix`) read it from the API.
- Voice tools call the same services as the API; they never bypass masking or audit.
- No frontend code or types in this repo.

## 8. Key Decisions
| Decision | Reason | Alternatives rejected |
|---|---|---|
| SQL (SQLAlchemy) over Firestore | Dashboards need joins/aggregations; instant `make reset`; tests without emulators | Firestore (discussed 29 Oct 2026) |
| JWT in httpOnly cookie, same-origin via Next.js rewrite | No token in JS; CSRF limited by SameSite=Lax | Bearer tokens in localStorage |
| JWT carries `role` + `landing` | Next.js proxy routes without a backend call | Proxy calling `/auth/me` per request |
| `app.state.session_factory` | Tests and audit middleware share one DB override point | `dependency_overrides` (not visible to middleware) |
| `UTCDateTime` column type | SQLite drops tzinfo; all stored times are UTC, shown in IST | Naive datetimes |
| Money as integer rupees | No float rounding; premiums are whole rupees | Decimal/paise |

## 9. Failure Scenarios
- **DB unavailable:** request fails with 500; nothing partial is committed (one session per request).
- **Gemini unavailable (Phase 2+):** call cannot start → clear error in the console; post-call analysis is retried / marked pending, transcript is still saved.
- **Expired/invalid JWT:** 401; frontend redirects to `/login`.
