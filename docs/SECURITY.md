# Security

## 1. Authentication
- Email + password (bcrypt via passlib). Seed users share `SEED_PASSWORD`.
- On login the API sets cookie `av_session`: JWT HS256, 8h expiry, `HttpOnly`, `SameSite=Lax`, `Secure` when `COOKIE_SECURE=true`.
- Unknown email and wrong password return the same 401 message and similar timing (dummy hash check).
- Inactive users get 401 on every request (the user is reloaded from the DB each time).

## 2. Authorization
- **Server-side, on every endpoint** with `Depends(require(Screen.X, *levels))`. The matrix in `app/core/rbac.py` is the only source; the frontend menu reads it from `/api/v1/auth/me`.
- Wrong role → **403**. Not signed in → **401**.
- `own` level (human_agent on calls): queries must filter by `agent_user_id == user.id`.
- Maker-checker: makers (bot_designer, campaign_manager) can never approve; only ops_manager / compliance_officer can.

## 3. Masking
Masked roles (ops_manager, campaign_manager, qa_analyst, compliance_officer on customer data): phone `98XXXXXX21`, masked email, no date of birth, no search by phone. Masking happens in the API, never in the UI.

## 4. Voice agent guardrails
- Nothing about a policy is returned by any tool before `verify_customer` succeeds on this call (enforced in `services/voice_tools.py`, not just the prompt).
- Renewal calls verify only the called customer; 3 failed attempts lock verification; the spoken DOB is not stored in tool payloads.
- Only the agent who started a call can connect, take over or end it (others get 404).
- Payment links are sandbox URLs; WhatsApp sends are recorded as follow-ups only.

## 5. Audit (append-only `audit_log`)
Recorded: login, failed login, logout, customer view, create/update/delete, submit, approve, reject, launch, export (the CSV export is itself audited), takeover, call start/end. Explicit `audit.record` for named actions; middleware catches any other successful authenticated write. Never log passwords, tokens, API keys or full PII in `detail`.

## 6. Secrets and Environment
| Variable | Private | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | yes | Gemini; only in `.env` |
| `JWT_SECRET` | yes | long random string |
| `SEED_PASSWORD` | yes | persona password |
| `DATABASE_URL` | yes | |
| `FRONTEND_ORIGIN` | no | CORS allow-list (single origin, credentials on) |
| `COOKIE_SECURE` | no | true behind HTTPS |
Only `.env.example` is committed. Never paste keys into prompts or chat.

## 7. API Security
- Input validated by Pydantic (types, lengths, `EmailStr`, bounded pagination).
- CORS: only `FRONTEND_ORIGIN`. The browser normally uses the same-origin Next.js rewrite.
- Errors return a plain message; no stack traces or SQL in responses.
- **Not done (sandbox):** login rate limiting / lockout. Add before any internet-facing deployment.

## 8. Checklist
- [x] Authentication · [x] Server-side authorization · [x] Server-side validation · [x] Secrets in env · [x] Masking · [x] Audit
- [ ] Rate limiting (not in sandbox) · [ ] Alembic migrations before shared DB
