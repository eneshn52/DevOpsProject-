# Project status and implementation roadmap

Updated: 18 October 2026.

The project currently provides a local coding workspace with PostgreSQL persistence, chat with a configured master model, and validated task-plan generation. The master can produce and save a dependency plan. Automatic assignment to remote agents and task execution are future work.

## Intended system

One central Django application coordinates a local master model and approximately 4-5 specialist agents on separate computers through APIs. PostgreSQL stores shared project and execution state. The reported hardware budget is 6 GB VRAM and 16 GB RAM; the remaining computers' capacities must be recorded individually.

The intended workflow is:

```text
User request
  -> Master creates a plan                    IMPLEMENTED
  -> Django validates and saves tasks         IMPLEMENTED
  -> Router selects suitable specialists      FUTURE
  -> Scheduler releases runnable tasks        FUTURE
  -> Remote agents produce artifacts          FUTURE
  -> Tests, review, and integration            FUTURE
  -> Completed project and final report       FUTURE
```

## Implemented and usable

| Area | What works now |
| --- | --- |
| Workspace interface | Original Codex/Antigravity-inspired layout with sidebar, conversation area, agent panel, dark/light themes, responsive layouts, keyboard shortcuts, settings, and labelled dialogs. Uses Django templates, native JavaScript, and CSS. |
| Project records | Create/select projects in the UI; store names, requirements, summaries, and branch metadata. Project archive/restore is available through the API. |
| PostgreSQL persistence | Projects, conversations, messages, endpoint configurations, agents, chat requests, plans, tasks, and dependencies are stored on the server. Schema migrations through 0006 were applied during implementation. |
| Conversation history | Saved messages and replies, conversation search, paginated history, deletion and restoration, and reload persistence. |
| Master configuration | Explicitly select the master model and an API connection, or use the default local Ollama endpoint. No automatic selection of a master when none is configured. |
| Specialist configuration | Save up to five specialists with model, role, machine label, API reference, and optional context-window setting. This stores configuration; it does not verify availability. |
| API connections and keys | Save endpoint URLs and keys in PostgreSQL. Keys stay on the server, are sent as Bearer credentials when calling the model, and are omitted from API responses. Blank edits preserve a key; explicit removal clears it. Storage is plaintext, as requested for the local project. |
| Master chat | Django sends bounded conversation context to the configured Ollama-compatible master endpoint, then saves its reply or a safe failure message. |
| Duplicate-request handling | Repeating the same chat or planning request ID and content replays its result without another generation. Conflicting reuse is rejected. |
| Task-plan generation | The master receives a project request and bounded project context, and returns structured JSON containing 1-20 tasks. The UI shows saved history, details, generation state, and failures. |
| Task requirements | Each planned task stores its specialization, instructions, required capabilities, expected output, acceptance criteria, and prerequisite task references. |
| Task dependencies (FR-12) | The master records direct prerequisites in `dependsOn`. Django rejects invalid graphs and saves tasks and dependency edges atomically. Saved dependencies survive reload/replay. Assignment admits a dependent task only after every prerequisite is `COMPLETED`, allowing independent branches in parallel. |
| Access and validation | Shared workspace APIs enforce input validation and CSRF for writes. Local loopback access is permitted by configuration; remote browser access requires a Django staff session. This is separate from future worker authentication. |
| Browser-data migration | Explicit import of older browser-local chats and settings, with duplicate-import protection. Old browser data remains until import succeeds. Browser storage now holds appearance/selection preferences and the import retry ID. |
| Administration and documentation | Django admin, API documentation, design specification, and brief explanations on Python functions and JavaScript functions/callbacks. Generated plan tasks and dependencies are protected from manual admin edits. |
| Docker runtime | Application image with Gunicorn, WhiteNoise/static assets, non-root execution, startup migrations, and readiness checking. Compose runs Django/PostgreSQL with the existing data volume. Windows startup/configuration script and optional Ollama CPU/NVIDIA override files are provided; the default host-Ollama setup was executed and verified. See `DOCKER.md`. |

## Foundations that exist but need working services

A database table or status field is a starting point for these features, not a complete implementation.

| Existing foundation | What still needs to be connected |
| --- | --- |
| Agent status and heartbeat fields | Actual health probes, worker heartbeats, freshness rules, and availability updates. |
| Capability metrics and agent scores | Capability profiles, benchmark collection, and selection based on measured suitability. |
| Task assignment, status, and attempt-count fields | Assignment service, execution attempts, capacity reservations, scheduling, and recovery. |
| Tasks and dependency records | Recording/validating dependencies and filtering runnable tasks are implemented. Transferring prerequisite artifacts to workers and automatically retrying failed prerequisites remain future work. |
| Code artifacts and artifact reviews | Artifact upload/retrieval, repository changes, automated checks, reviewer execution, and integration. |
| Execution metrics | Automatic recording of durations, token usage, failures, and retries during worker execution. |
| Git branch metadata | Creating branches/worktrees, synchronizing repository versions, collecting patches, and merging accepted changes. |

## Future implementation, in recommended order

### 1. Verify remote connectivity and worker identity

Implement endpoint health checks and model discovery. Distinguish a reachable model endpoint from a worker capable of editing files or running tools. For workers, define an authenticated registration/heartbeat API and record machine identity, available models, context limits, execution capacity, and last successful contact. Show verified state in the frontend and expire stale availability.

Done when: the central application can verify each configured computer, identify its models/capacity, and detect a disconnected worker.

### 2. Implement capability-based routing

Give specialists explicit capability profiles. Let the master classify a task and recommend a registered agent using structured output. Django validates capability fit, context constraints, and availability, then records the decision and brief rationale. Define what happens when the best suitable agent is busy or unavailable. Start with configured scores and add measured evaluation results later.

Done when: a task selects an eligible specialist through a tested, repeatable workflow, with unsupported or unavailable tasks handled explicitly.

### 3. Implement the background scheduler

Run a scheduler separately from HTTP requests. Find tasks whose prerequisites have successfully completed, route them, and atomically reserve both the task and computer capacity. Commit before making network calls. Run independent work concurrently across machines, initially with one active generation per computer. Persist changes so scheduler restarts do not lose the queue.

Done when: dependency order is respected, independent tasks run in parallel, and competing schedulers cannot dispatch the same task or over-reserve a worker.

### 4. Add the worker execution and recovery protocol

Define task submission, execution lookup, heartbeat, result delivery, and cancellation APIs. Add per-attempt identifiers, deadlines, reservations with expiry, bounded retries, and explicit blocked/failed/cancelled behavior. Workers must recognize repeated submissions of the same attempt. Reject stale results and account for generation that may still be running after a network timeout.

Done when: a worker disconnect or scheduler restart does not lose task history, accept obsolete output, or cause uncontrolled duplicate work.

### 5. Give agents a controlled project workspace

Provide repository snapshots or checkouts, explicit file access, and isolated work areas. Define how agents read files, propose changes, run commands, and return artifacts. Associate each artifact with the task, attempt, and repository revision. Pass relevant prerequisite outputs to dependent tasks and prevent concurrent workers from overwriting the same checkout.

Done when: a remote agent can make a reviewable project change and return a reproducible artifact tied to the correct input revision.

### 6. Connect tests, review, and integration

Run the appropriate syntax, build, and test checks against generated changes. Have a review agent assess artifacts and acceptance criteria; return actionable failures for bounded repair attempts. Integrate accepted work, handle conflicts, and perform final project-level verification. Model-generated claims of success must be checked against actual execution results.

Done when: accepted changes are supported by recorded checks and reviews, and the combined project passes its final verification.

### 7. Complete live progress and operational visibility

Stream model output and task events to the frontend, with reconnect/replay behavior. Show assignments, queue state, dependencies, attempts, failures, artifacts, reviews, and execution metrics. Provide explicit run/cancel/retry controls and a final project report. These UI additions can be developed alongside the scheduler and workers.

Done when: the user can follow and control an entire distributed run, including after closing and reopening the browser.

### 8. Verify the complete system on the actual computers

Exercise real master planning, specialist selection, parallel execution, dependency handoff, code review, integration, disconnections, timeouts, duplicate delivery, and restart recovery. Measure model quality and memory use on each machine and adjust context/concurrency limits accordingly. Document local startup, worker setup, backups/restoration, and a repeatable demonstration.

Done when: a representative request produces a verified project across the intended 4-5 computers, including recovery from a deliberately interrupted worker.

## Current limits and verification evidence

- The latest recorded suite passed 51 tests on PostgreSQL, both natively and inside the Linux application container, covering workspace APIs, access controls, request replay, planning validation, concurrency, migration preservation, readiness, and model-address translation. Django checks, migration drift checks, Python compilation, JavaScript syntax, and function-explanation auditing also passed. Updating this status document does not itself represent a new test run.
- Browser verification covered saved plans, task details, reload persistence, invalid-plan failures, and a narrow responsive layout. Planning browser tests used a clearly labelled mocked model in a disposable PostgreSQL database; they did not evaluate real model quality.
- Chat and plan generation currently run synchronously in the Django request. Closing the browser does not itself cancel server generation. Server-process failure is not handled by a durable execution service; stale reservations are marked failed on later access and require a new attempt.
- Plan generation is bounded to 20 tasks and limited project context. Interactive plan editing, recursive decomposition, repository-aware context retrieval, and automatic replanning remain future enhancements.
- A saved `READY` plan means the plan has been saved successfully. It does not mean its tasks have executed. Saved specialist configurations do not mean those computers are online.
- The project currently has one shared workspace. Multi-tenant accounts, public hosting, and database-key encryption are outside the agreed local scope.

## Code and documentation map

| File | Responsibility |
| --- | --- |
| `django_modules/models.py` | Persistent workspace, agent, plan, task, dependency, artifact, review, and metric records. |
| `django_modules/workspace_api.py` | Workspace/project/chat-history/configuration APIs, access checks, validation, and browser import. |
| `django_modules/chat_service.py` | Master-model transport and persisted chat exchanges. |
| `django_modules/task_planning.py` | Structured plan generation, validation, history, and atomic task persistence. |
| `django_modules/static/workspace/workspace.js` | Browser state, API calls, configuration forms, conversations, and task-plan interaction. |
| `templates/index.html` and `django_modules/static/workspace/workspace.css` | Workspace markup and styling. |
| `django_modules/tests.py`, `test_workspace_api.py`, and `test_task_planning.py` | Existing model, workspace, planning, concurrency, and migration tests. |
| `WORKSPACE_API.md` | API contracts, setup, credential behavior, and implementation limits. |
| `DESIGN.md` | Interface design tokens and conventions. |
| `Dockerfile`, `docker-compose.yml`, and `docker/` | Container build, local service orchestration, startup scripts, and environment initialization. |
| `DOCKER.md` | Local Docker startup, sign-in, persistent data, backups, optional Ollama/GPU setup, and verification results. |

The next recommended implementation is verified agent connectivity, followed by routing and background scheduling. The saved planning foundation is ready for those services.
