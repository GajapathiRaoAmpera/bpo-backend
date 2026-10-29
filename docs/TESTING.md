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

New endpoints add: success, unauthenticated (401), forbidden role (403), invalid input (422), and an audit assertion for named actions.

## Commands
`make test` (= `poetry run pytest -q`). Run after every phase.

## Definition of Done
- [ ] `make test` green · [ ] `make reset` works · [ ] API starts (`make dev`) and `/openapi.json` loads
