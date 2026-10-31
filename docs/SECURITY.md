# Security

## 1. Authentication
- Email + password (bcrypt via passlib). Seed users share `SEED_PASSWORD`.
- On login the API sets cookie `av_session`: JWT HS256, 8h expiry, `HttpOnly`, `SameSite=Lax`, `Secure` when `COOKIE_SECURE=true`.
- Unknown email and wrong password return the same 401 message and similar timing (dummy hash check).
- Inactive users get 401 on every request (the user is reloaded from the DB each time).
- Login rate limit: `LOGIN_RATE_LIMIT` (default `5/minute`) per client IP (first `X-Forwarded-For` entry, else the socket peer) via slowapi; over the limit → 429. In-memory counters: valid for the single Cloud Run instance only.
- Email lockout: 10 failed logins for an email within 15 minutes (counted from `login_failed` audit rows) → further attempts get the same 401, even with the right password. Locked attempts are also audited, so the lock lasts until 15 minutes of quiet.

## 2. Authorization
- **Server-side, on every endpoint** with `Depends(require(Screen.X, *levels))`. The matrix in `app/core/rbac.py` is the only source; the frontend menu reads it from `/api/v1/auth/me`.
- Wrong role → **403**. Not signed in → **401**.
- `own` level (human_agent on calls): queries must filter by `agent_user_id == user.id`.
- Maker-checker: makers (bot_designer, campaign_manager) can never approve; only ops_manager / compliance_officer can.
- User admin (super_admin): users are deactivated, never deleted; deactivation takes effect on the user's next request. Nobody can deactivate themselves or change their own role.
- Live monitor: masked roles (ops_manager) get masked phones and no customer speech in the transcript snippet.

## 3. Masking
Masked roles (ops_manager, campaign_manager, qa_analyst, compliance_officer on customer data): phone `98XXXXXX21`, masked email, no date of birth, no search by phone. Customer details shown with calls/escalations follow the role's Customer 360 level (roles without full customer access get masked data), and a spoken date of birth is replaced with `[date of birth]` in transcripts for those roles. Call-detail and policy views are audited as `view_customer`. Masking happens in the API, never in the UI.

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
- Security headers on every response (backend middleware and `next.config.ts` `headers()`): `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and HSTS (backend: only when `COOKIE_SECURE=true`).
- `APP_ENV=production` disables `/docs`, `/redoc` and `/openapi.json`. Generate frontend types against sandbox.

## 8. Checklist
- [x] Authentication · [x] Server-side authorization · [x] Server-side validation · [x] Secrets in env · [x] Masking · [x] Audit
- [x] Login rate limiting + email lockout · [x] Security headers · [x] API docs off in production · [ ] Alembic migrations before shared DB
