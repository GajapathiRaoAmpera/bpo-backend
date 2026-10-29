FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 POETRY_VIRTUALENVS_CREATE=false
WORKDIR /app
RUN pip install --no-cache-dir "poetry==1.8.2"
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-interaction --no-ansi
COPY app ./app
EXPOSE 8000
# Seeds a fresh sandbox if the DB is empty, then serves the API.
CMD ["sh", "-c", "python -c 'from app.core.db import engine; import sqlalchemy as sa; raise SystemExit(0 if sa.inspect(engine).has_table(\"users\") else 1)' || python -m app.seed; uvicorn app.main:app --host 0.0.0.0 --port 8000"]
