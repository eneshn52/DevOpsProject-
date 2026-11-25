# FR-13: task types and required capability weights

- [x] Use the existing type-to-weight table as the shared classification policy, including GENERAL.
- [x] Store four capability weights per task and backfill existing tasks without changing legacy scalar weights.
- [x] Connect planner, scheduler, API responses, admin, and task-plan details to the stored weights.
- [x] Verify classification, migration preservation, and compatibility with FR-14 and worker APIs.

The master selects a validated task type; Django assigns its capability weights. No additional model call or dependency is needed.

Validation: 141 tests passed, 1 skipped, 65 subtests passed on PostgreSQL with DB_HOST=127.0.0.1. Django checks, migration consistency, JavaScript syntax, and git diff checks passed. The default localhost connection timed out in live worker tests on this Windows environment; the same login timeout reproduced on unmodified HEAD. Using IPv4 resolved all five worker tests without code changes.

Migration 0008 was exercised in the isolated test database. The application database and running Docker image have not been upgraded.
