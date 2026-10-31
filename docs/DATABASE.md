# Database

Source of truth for the data model: `app/models/__init__.py` and `app/models/enums.py`. Do not invent tables or fields; update this file when the schema changes.

## 1. Engine
SQLite (`sqlite:///./assurevoice.db`) for the sandbox. PostgreSQL (e.g. Supabase, driver `psycopg`) by changing `DATABASE_URL` to `postgresql+psycopg://...`; no SQLite-only SQL. Enums are strings on SQLite and native enum types on Postgres.
To load the sandbox into a remote Postgres, seed SQLite and bulk-copy it: `DATABASE_URL=sqlite:///./assurevoice.db poetry run python -m app.seed && poetry run python -m scripts.copy_to_postgres` (the row-by-row seed is too slow over the network).

## Migrations (Alembic)
Every schema change needs a migration: change the model, then `poetry run alembic revision --autogenerate -m "what changed"`, review the file, and `make migrate`. `tests/test_migrations.py` fails if models and migrations drift apart.
- Adding a value to an enum is not autodetected: on Postgres write `op.execute("ALTER TYPE <name> ADD VALUE '<value>'")` by hand.
- The Docker image runs `alembic upgrade head` on start. `make reset` / `python -m app.seed` still rebuild tables with `create_all` (sandbox only; refuses under `APP_ENV=production` without `--i-know`).
- A database created before migrations existed (e.g. the Supabase staging DB) is adopted with `alembic stamp head` after checking it has no drift.

## 2. Conventions
- Tables: plural snake_case. Columns: snake_case. PK: `id` integer (except `qa_scores.call_id`).
- FKs: `<entity>_id`; user references: `created_by`, `reviewed_by`, `assigned_to`, `agent_user_id`, `actor_user_id`.
- Timestamps: `*_at`, stored UTC via `UTCDateTime`; always timezone-aware in Python. Display in IST.
- Money: integer rupees, suffix `_inr`.
- Enums: Python `StrEnum`, member name == value (SQLAlchemy stores names).

## 3. Tables
| Table | Purpose | Key columns |
|---|---|---|
| users | Personas / staff | email (unique), full_name, job_title, role, password_hash, is_active, last_login_at |
| customers | Policyholders | customer_code (unique), full_name, phone, email, dob, city, state, preferred_language |
| policies | Policies | policy_no (unique, `MOT-2026-001234`), customer_id, product, plan_name, sum_insured_inr, premium_inr, status, start_date, renewal_date, vehicle_reg |
| scripts | Bot system prompts (maker-checker) | name, mode, version, system_prompt, status, parent_id, created_by, submitted_at, reviewed_by, reviewed_at, review_note, checklist |
| campaigns | Outbound renewal campaigns (maker-checker) | name, product, segment (JSON), script_id, status, created_by, reviewed_by, reviewed_at, scheduled_at, total_targets, dialled, connected, converted |
| campaign_targets | Policies in a campaign | campaign_id, policy_id (unique together), status, call_id |
| calls | Every call | call_ref (unique), direction, mode, customer_id, policy_id, agent_user_id, campaign_id, script_id, status, started_at, ended_at, duration_sec, outcome, sentiment, summary, next_action, verified, taken_over_at, is_simulated |
| transcript_turns | Call transcript | call_id, seq, speaker (bot/customer/agent/tool), text, offset_ms, tool_name, tool_payload |
| qa_scores | 5-point QA per call | call_id (PK), ai_disclosure, identity_verified, correct_figures, no_mis_selling, polite_close, score, source (ai/human), reviewer_id, reviewer_note, locked_at |
| escalations | Human hand-offs | call_id, customer_id, policy_id, reason, priority, status, assigned_to, resolved_at, resolution_note |
| followups | Payment links, callbacks, reminders | policy_id, customer_id, call_id, kind, due_at, reason, status, message_text, payment_link_url, created_by (null = AI), assigned_to, sent_at |
| audit_log | Append-only audit trail | ts, actor_user_id, actor_role, action, entity_type, entity_id, detail (JSON), ip, user_agent |
| integrations | Integration cards | key (unique), name, category, status (connected/pilot), description, last_sync_at |

## 4. Relationships
customers 1─* policies · customers 1─* calls · policies 1─* calls · calls 1─* transcript_turns · calls 1─1 qa_scores · calls 1─* escalations / followups · campaigns 1─* campaign_targets · scripts 1─* campaigns · scripts → scripts (parent_id, previous version).

## 5. Rules
- **Active script:** latest `approved` script for the mode by `reviewed_at` (`services/scripts.active_script`). Pending/draft scripts are never used by the bot.
- **Campaign progress:** counts targets, not attempts. `dialled` = targets called at least once; a no-answer target can be retried.
- **Soft delete:** no. Users are deactivated (`is_active=false`), never deleted, so audit references stay valid.
- **Auditing:** `audit_log` is append-only; no API updates or deletes it.

## 6. Sensitive Data
`customers.phone`, `customers.email`, `customers.dob`, transcripts. Masked roles get phone `98XXXXXX21`, email `d***@domain`, no DOB, and cannot search by phone (`core/masking.py`, `schemas/customer.py`).

## 7. Migrations
Sandbox: schema is recreated by `make reset` (drop + create + seed). Before any shared/production database, introduce Alembic; schema changes then need a migration and a test.

## 8. Seed Data (`app/seed`)
Deterministic (`rng_seed=2026`), dates relative to today: 8 personas; 500 customers (Indian names, ~60% Tamil Nadu, English/Tamil/Hindi); 1–3 policies each, renewals over the next 60 days (plus some renewed/lapsed); 30 days of ~1,200 calls inside 09:00–19:00 IST with transcripts, summaries, QA scores; matching follow-ups, escalations (some open), campaigns in every status, scripts (v1 approved, renewal v2 pending), audit entries, integrations. Runs in ~2s.
