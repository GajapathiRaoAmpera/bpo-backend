# Code Style (backend)

## Principles
Readable, focused functions; reuse `core/` helpers; no abstraction until a second real use; consistency over novelty.

## Naming
- Modules/functions/variables: `snake_case`. Classes: `PascalCase`. Constants: `UPPER_SNAKE`.
- Routes: plural nouns under `/api/v1` (`/customers/{id}`); actions as sub-paths (`/scripts/{id}/approve`).
- Pydantic: `XxxIn` (request), `XxxOut` / `XxxRow` / `XxxDetail` / `XxxPage` (response).

## Patterns
- Every endpoint declares its access with `require(Screen.X, ...)`; never check roles ad hoc.
- Build masked responses with the schema helper (`customer_fields`), never in routers.
- Named actions call `audit.record(...)`; don't rely on the middleware for them.
- Money via `core.formatting.format_inr`; dates `format_date`; store UTC, show IST.
- Errors: `HTTPException` with a user-facing sentence that doesn't blame the user.
- Comments explain *why*; a known limit gets `# shortcut: <limit>, <when to upgrade>`.

## Tooling
Poetry; pytest. Pin exact versions in `pyproject.toml`.

## Don't
Duplicate the access matrix, add dependencies for a few lines, change unrelated files, or commit `.env` / `*.db`.
