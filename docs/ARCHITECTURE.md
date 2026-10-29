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

## 9. Voice agent (Phase 2)
```
Console ──POST /voice/sessions──> Call row (in_progress) + LiveSession (in-memory registry)
Console ──Pipecat JS client: POST/PATCH /voice/sessions/{id}/offer──> SmallWebRTCRequestHandler
        └─ first offer spawns app/voice/bot.run_bot:
           SmallWebRTCTransport.input → LLMUserAggregator(Silero VAD) → GeminiLiveLLMService(+5 tools)
           → transport.output → LLMAssistantAggregator        (PipelineWorker + WorkerRunner, Pipecat 1.15)
Tools ──> app/services/voice_tools.py (DB; guardrails in code) ──> tool TranscriptTurn + RTVI server message
Turns ──> on_user_turn_message_added / on_assistant_turn_stopped ──> transcript_turns (offset = turn start)
End/disconnect ──> voice/session.finalize (idempotent) ──> post_call.analyse_call (background)
```
- **Prompt** = latest approved script for the mode + call context (renewal: customer/policy as *unverified* context) + date/time in IST + tool rules.
- **Guardrails in code:** policy tools refuse until `verify_customer` succeeds for this call; renewal calls can only verify the called customer; 3 failed attempts lock verification; spoken figures come from `*_spoken` tool fields.
- **Takeover:** marks the call, stops the bot, the human wraps up with an outcome (+ link/callback created on their behalf) and a note.
- **Post-call:** Gemini Flash (low thinking) returns `{summary, outcome, sentiment, next_action, qa}`; primary model then fallbacks, 3 rounds over ~1 min. Tool evidence and agent choice override the model's outcome; `identity_verified` comes from the call record. If every attempt fails, the call keeps a deterministic outcome and QA is left unscored (never faked).
- **Models** (env): `GEMINI_LIVE_MODEL` (default `models/gemini-3.8-live`), `GEMINI_LIVE_VOICE`, `GEMINI_FLASH_MODEL`, `GEMINI_FLASH_FALLBACK_MODELS`.
- shortcut: the live-session registry is in-process, so run one API worker; move it to Redis or a call service to scale out.

## 10. Failure Scenarios
- **DB unavailable:** request fails with 500; nothing partial is committed (one session per request).
- **Gemini Live unavailable:** the bot can't start; the console shows the connection error and the call is closed as no answer.
- **Gemini Flash overloaded (503/504):** analysis retries across models; the console shows "Analysing" meanwhile; worst case the outcome comes from tool evidence and QA stays unscored.
- **Browser closes mid-call:** WebRTC disconnect stops the bot and finalises the call; a never-connected call is closed when the agent starts the next one.
- **Expired/invalid JWT:** 401; frontend redirects to `/login`.
