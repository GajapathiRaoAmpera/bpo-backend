# Testing (backend)

## Goals
Prove the rules the product depends on: authentication, RBAC (403s), masking, audit, maker-checker; later the voice tools and post-call analysis.

## Tools and Layout
pytest + FastAPI `TestClient`. `tests/conftest.py` builds a temp SQLite DB and seeds a small deterministic dataset (40 customers, 120 calls).

| File | Covers |
|---|---|
| `test_auth.py` | all 8 personas log in, landing + menu, cookie flags, 401s, login/logout audited |
| `test_rbac.py` | 403 per role on protected endpoints, menu == matrix, matrix matches PRD, customer view audited |
| `test_masking.py` | `98XXXXXX21`, no DOB/email for masked roles, full data for full roles, no phone search when masked |
| `test_maker_checker.py` | bot ignores pending scripts; approval switches the active script |
| `test_audit.py` | middleware audits unaudited writes once; skips anonymous/failed writes |
| `test_voice.py` | tool guardrails (no details before verify, lockout, cross-customer), parsing, outcome precedence, voice API RBAC/ownership, takeover + agent outcome, analysis with mocked Gemini |
| `test_phase3_api.py` | dashboard/calls/escalations/followups RBAC (401/403), policy + call detail masking (incl. DOB in transcripts) and audit, HA own calls only (404 others), call filters, claim/resolve rules + audit, follow-up create/patch validation + audit |
| `test_phase3_scheduler.py` | follow-up timer job called directly: due → sent with template, future untouched, Gemini only for fresh follow-ups, template fallback on Gemini failure |
| `test_phase4_maker_checker.py` | campaign/script/approvals RBAC (401/403), draft-only edits, checklist + note rules, self-approval blocked, approval switches the active script, launch places 3 simulated calls and updates progress, audit entries |
| `test_phase4_qa.py` | QA queue, override + lock (locked → 409), view roles can't write, DOB redacted in transcripts, audit |
| `test_phase5_admin.py` | users/integrations SA-only, create/patch/deactivate audited, password hashed + never logged, self-deactivate/demote blocked, deactivated user can't sign in |
| `test_phase5_audit_live.py` | audit + live RBAC, audit filters/pagination/422s, CSV export content + audited, live calls with masking |

New endpoints add: success, unauthenticated (401), forbidden role (403), invalid input (422), and an audit assertion for named actions.

## Commands
`make test` (= `poetry run pytest -q`). Run after every phase. Tests never call Gemini (`GOOGLE_API_KEY` is blanked in `conftest.py`).

Live voice e2e (real Gemini): `poetry run python -m scripts.make_test_audio /tmp/av-audio` generates the customer's side of a call; then run the opt-in Playwright spec in bpo-frontend (`E2E_VOICE_DIR=/tmp/av-audio`).

## Definition of Done
- [ ] `make test` green · [ ] `make reset` works · [ ] API starts (`make dev`) and `/openapi.json` loads
