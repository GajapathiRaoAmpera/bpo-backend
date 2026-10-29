# AI Coding Agent Instructions (bpo-backend)

**Project:** AssureVoice AI sandbox: FastAPI backend + Pipecat/Gemini voice agent for an Indian insurer's AI contact centre. All data is synthetic. The frontend is a separate repo (`bpo-frontend`); never put frontend code here.

## Source of truth (read before significant changes)
- `docs/PRD.md`: product, roles, screens, phases, definition of done
- `docs/ARCHITECTURE.md` · `docs/DATABASE.md` · `docs/SECURITY.md` · `docs/API.md`
- `docs/CODE_STYLE.md` · `docs/TESTING.md`

If a request conflicts with these docs, say so and ask; don't guess.

## Non-negotiables
- Every endpoint: `Depends(require(Screen.X, ...))`. Access matrix lives only in `app/core/rbac.py`.
- Masked roles never receive full phone, email or DOB (masking in the API).
- Audit every login, customer-data view, change, approval and export.
- Maker-checker: bot uses only the latest approved script (`services/scripts.active_script`).
- Voice agent: discloses automation + recording, verifies before sharing anything, states only tool-returned figures.
- INR as ₹1,25,000; dates "29 Oct 2026"; store UTC, display IST.
- Secrets only in `.env`. Pin dependency versions in `pyproject.toml` (Poetry).
- Check current official docs for Pipecat and Gemini Live before using their APIs.
- Work phase by phase; don't start the next phase until the user says so.

## Workflow
1. Read the relevant docs and code; list affected files, schema/API changes and tests.
2. Make focused changes that reuse existing patterns.
3. Validate: `make test`, `make reset`, start `make dev`, hit the endpoints.
4. Update docs when behaviour, schema, API or security rules change.
5. Report: what changed, what was tested, what is simulated, open questions.

## Commands
`make setup` · `make dev` (port 8000) · `make reset` · `make test`
