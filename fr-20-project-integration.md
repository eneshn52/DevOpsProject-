# FR-20: integrate all task outputs into one runnable project

Reuse FR-18 artifacts and the project branch, FR-12 dependencies, and the FR-16 result hook. Keep worker endpoints and result formats unchanged.

- [x] Merge the newest artifact of every task in dependency order; dependents may replace prerequisite files, independent writers conflict.
- [x] Check the merged tree without executing it: Python syntax, JSON, local HTML links, and a detected start command.
- [x] Write README.md, a combined requirements.txt, and integration.json; commit once to the project branch.
- [x] Record each run (`ProjectIntegration`, migration 0011), reuse runs with identical inputs, and retry after Git failures.
- [x] Run automatically after a plan's last task result; expose GET/POST `/api/projects/{id}/plans/{uuid}/integration/` and an **Integrate project** button.

Scope: assembling and statically checking the project. Running its tests (FR-21), review agents, and automatic repair of conflicts remain separate features.

Verified on SQLite (`USE_SQLITE=True`; PostgreSQL and Docker were not running on this machine): 27 new tests, full suite 267 passed, 3 skipped, 68 subtests. Django checks, migration drift check, JavaScript syntax, and git diff checks passed. A merged three-task sample (stdlib HTTP backend, static frontend, pytest test) was written out, its start command served HTTP 200, and its test command passed.
