# AssureVoice AI · Backend

FastAPI service for the AssureVoice AI sandbox: data, RBAC, PII masking, audit log, maker-checker and (from Phase 2) the Pipecat + Gemini Live voice agent. All data is synthetic.
The UI lives in the separate **bpo-frontend** repo; the contract between them is `/openapi.json`.

## Requirements
Python 3.11+ (3.12 recommended), Poetry 1.8+.

## Setup and run
```bash
make setup     # poetry install, create .env from .env.example if missing, seed the sandbox
# edit .env: set JWT_SECRET, SEED_PASSWORD (and GOOGLE_API_KEY from Phase 2)
make reset     # reseed after changing SEED_PASSWORD
make dev       # http://localhost:8000  (docs at /docs, schema at /openapi.json)
make test      # pytest
```

| Variable | Purpose |
|---|---|
| `JWT_SECRET` | Signs the session cookie (long random string) |
| `SEED_PASSWORD` | Password for all 8 personas |
| `DATABASE_URL` | Default `sqlite:///./assurevoice.db`; PostgreSQL URL works too |
| `FRONTEND_ORIGIN` | CORS allow-list, default `http://localhost:3000` |
| `COOKIE_SECURE` | `true` behind HTTPS |
| `GOOGLE_API_KEY` | Gemini Live voice agent and Gemini Flash post-call analysis |
| `GEMINI_LIVE_MODEL`, `GEMINI_LIVE_VOICE`, `GEMINI_FLASH_MODEL`, `GEMINI_FLASH_FALLBACK_MODELS` | Optional model overrides (defaults in `.env.example`) |

Docker: `docker build -t assurevoice-backend . && docker run --env-file .env -p 8000:8000 assurevoice-backend` (seeds on first start).

## Sandbox data
`make reset` drops and reseeds everything in ~2 s: 8 personas, 500 customers, ~850 policies (renewals over the next 60 days), 1,200 calls over 30 days with transcripts, summaries and QA scores, plus follow-ups, escalations, campaigns, scripts, audit entries and integrations. Deterministic, with dates relative to today.

## Persona logins
Emails `firstname.lastname@sandbox.assurevoice.in`, password `SEED_PASSWORD`. Full table and walkthrough in the bpo-frontend README.

## Project docs
`AGENTS.md` (agent rules) · `docs/PRD.md` · `docs/ARCHITECTURE.md` · `docs/DATABASE.md` · `docs/SECURITY.md` · `docs/API.md` · `docs/CODE_STYLE.md` · `docs/TESTING.md`.
