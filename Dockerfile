FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 POETRY_VIRTUALENVS_CREATE=false POETRY_REQUESTS_TIMEOUT=300
WORKDIR /app
RUN pip install --no-cache-dir "poetry==1.8.2"
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-interaction --no-ansi
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
EXPOSE 8000
# Migrate, then (sandbox only) seed an empty DB, then serve the API.
CMD ["sh", "-c", "alembic upgrade head && (python -c 'from app.core.config import get_settings; from app.core.db import engine; import sqlalchemy as sa; raise SystemExit(get_settings().app_env != \"sandbox\" or bool(engine.connect().execute(sa.text(\"select count(*) from users\")).scalar()))' && python -m app.seed || true) && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
