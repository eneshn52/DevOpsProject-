# Saved task planning

Generate a validated, persistent dependency plan from a project request using the saved master model.

- [x] Add plan records and structured task requirements without changing existing tasks.
- [x] Add an idempotent generation API, bounded master context, and atomic DAG validation/persistence.
- [x] Add a task-plans dialog using the existing workspace styles, including saved history and failures.
- [x] Document the endpoints and verify invalid plans, retries, access controls, migrations, and the UI.

This stage saves plans and pending tasks. Scheduling and remote execution follow separately.

Verification: 46 PostgreSQL tests pass, including concurrent submissions and migration preservation. Django checks, migration drift checks, Python compilation, JavaScript syntax, and function-docstring auditing pass. Migration 0006 is applied; existing project/task/dependency/message counts remained [2, 2, 1, 18]. Browser checks in a disposable PostgreSQL database used a clearly labelled mocked planner to verify saved details, reload persistence, invalid-plan failures, and a 390px layout without horizontal overflow. No real model-quality evaluation was performed.
