.PHONY: setup dev reset test

setup:            ## install deps, create .env if missing, seed the sandbox
	poetry install --no-interaction
	@test -f .env || (cp .env.example .env && echo "Created .env - set JWT_SECRET and SEED_PASSWORD")
	poetry run python -m app.seed

dev:              ## API on http://localhost:8000 (docs at /docs)
	poetry run uvicorn app.main:app --reload --port 8000

reset:            ## wipe and reseed all synthetic data
	poetry run python -m app.seed

test:
	poetry run pytest -q
