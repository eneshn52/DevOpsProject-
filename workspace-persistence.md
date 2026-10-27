# PostgreSQL workspace persistence

## Goal
Make Django the source of truth for projects, conversations, master settings, API connections, and specialists while preserving the coding workspace UI.

## Work
- [x] Add additive models/migrations for connections, workspace configuration, conversations, and persisted chat requests; preserve existing projects/messages.
- [x] Implement validated same-origin JSON APIs with CSRF protection, server-side credentials, explicit errors, and request idempotency.
- [x] Replace browser data persistence with API calls; retain appearance preferences and offer an idempotent legacy-data import.
- [x] Connect project selection, settings CRUD, conversation search, delete/restore, and chat history to PostgreSQL.
- [x] Verify migration safety, API validation/access controls, chat failure/retry behavior, browser persistence, and PostgreSQL execution.

## Boundaries
Single shared workspace. Local loopback access is permitted for development; remote browser access requires a staff session. Worker registration, DAG execution, live health, and orchestration remain subsequent tasks.

Verified with 29 passing PostgreSQL tests, 28 passing SQLite tests plus one PostgreSQL-only skip, Django checks, JavaScript syntax checks, and browser flows against an isolated PostgreSQL database. Both migrations were applied to the working PostgreSQL database. Python functions and JavaScript functions/callbacks include brief explanations.
