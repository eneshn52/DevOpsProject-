# FR-12: the master records dependencies between tasks

Reuse the existing plan schema, TaskDependency table, atomic plan persistence, and FR-14 readiness query. Keep FR-13 weights and worker endpoints unchanged.

- [x] Clarify direct prerequisite semantics in the master's schema and instructions.
- [x] Verify exact saved edges, reload/replay persistence, plan isolation, and transactional rollback.
- [x] Exercise a generated branching/joining plan through prerequisite completion and failure.
- [x] Document FR-12 and run the PostgreSQL suite and Django migration checks.

Scope: recording and enforcing dependency order. Artifact transfer, worker execution, and automatic retries remain separate features.

Verified on PostgreSQL with DB_HOST=127.0.0.1: 40 focused tests passed; the full suite passed 144 tests and 68 subtests, with 1 development-only test skipped. Django system checks, Python compilation, migration consistency (no changes), and git diff checks passed. Model responses were mocked; real model dependency quality was not evaluated. No new migrations, Docker rebuild, or changes to application data were needed.
