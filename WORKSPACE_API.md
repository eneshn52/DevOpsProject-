# Shared workspace APIs

Workspace records live in PostgreSQL by default. Set database environment variables before starting Django. `.env.example` documents the options; the project does not automatically load `.env` files. `USE_SQLITE=True` explicitly enables the development backend.

## Start

```powershell
.\docker\start.ps1 -InitializeOnly
docker compose --env-file .env.docker up -d db
python manage.py migrate
python manage.py runserver
```

Migrations 0004 and 0005 add the schema and group existing messages into conversations. Existing projects and messages are retained. The master remains unconfigured until explicitly saved or imported.

For the complete application container, run `.\docker\start.ps1` and follow [Docker setup](DOCKER.md), including staff sign-in. The commands above start only the database for native Python development.

## Access

This release has one shared workspace. Loopback requests from `127.0.0.1` or `::1` may access it locally. Remote browser requests require a signed-in Django staff account. Configure `ALLOWED_HOSTS` and HTTPS for remote browser access. Set `WORKSPACE_ALLOW_LOCAL_ACCESS=False` behind a reverse proxy so proxy connections cannot qualify for the local bypass.

All writes require Django CSRF protection. The frontend sends `X-CSRFToken` and same-origin cookies. Worker registration and authentication are subsequent features.

## Resources

| URL | Methods | Behavior |
| --- | --- | --- |
| `/api/workspace/` | GET | Configuration, projects, and the first chat page; optional `projectId` |
| `/api/projects/` | GET, POST | List active projects or create one |
| `/api/projects/{id}/` | GET, PATCH, DELETE | Read/edit/archive; PATCH `archived: false` restores |
| `/api/projects/{id}/plans/` | GET, POST | List plan history or generate a saved plan with UUID `id` and `request` |
| `/api/projects/{id}/plans/{uuid}/` | GET | Read a saved plan and all its task requirements and dependencies |
| `/api/conversations/` | GET, POST | List/search chats or create with `projectId`, optional UUID `id` and `title` |
| `/api/conversations/{uuid}/` | GET, PATCH, DELETE | Read messages, rename, soft-delete; PATCH `deleted: false` restores |
| `/api/connections/` | GET, POST | List endpoints or save `name`, `url`, and optional `key` |
| `/api/connections/{uuid}/` | PATCH, DELETE | Edit/remove; `clearKey: true` removes a saved key |
| `/api/agents/` | GET, POST | Specialists with `name`, `modelName`, `apiId`, and `role`. Responses include `status`, `online` (heartbeat within 30 s), `lastHeartbeat`, and `lastSeenSeconds` (`null` if never) |
| `/api/agents/{id}/` | PATCH, DELETE | Edit or remove a specialist |
| `/api/agents/{id}/heartbeat/` | POST | Worker heartbeat every 10 s: `alive`, `status` (`IDLE`/`BUSY`), `currentTaskId` (required while busy). No CSRF token needed. Specialists silent for 30 s are marked `OFFLINE` |
| `/api/master/` | GET, PUT, DELETE | Read, configure, or remove the single master |
| `/api/chat/` | POST | Send `prompt`, `conversationId`, and UUID `requestId` |
| `/api/workspace/import/` | POST | Import UUID `id`, legacy `workspace`, optional `projectId` |

Responses use `{ "data": ... }`. Validation/access failures use `{ "error": "...", "code": "..." }`, optionally with field errors. A failed model exchange returns HTTP 502 with its persisted request and error reply inside `data`.

Chat lists accept `projectId`, `search`, `offset`, and `limit` (1–200); results contain `items` and `nextOffset`. Chat detail returns up to 200 recent messages and `olderCursor`. Pass `before={olderCursor}` for earlier messages.

## Credentials and model selection

Saved API keys are not returned by the API or written to browser preferences. Responses expose `hasKey`. Blank edits retain the existing key; `clearKey: true` removes it. Keys are stored as plaintext in the local PostgreSQL database, as intended for this local workspace.

Chat callers cannot provide arbitrary model names, URLs, or credentials. Django resolves them from the saved master and endpoint. A master without an endpoint uses `OLLAMA_DEFAULT_URL` on the Django computer. A configured model is not a verified live connection.

## Chat delivery

Messages and replies are persisted under their conversation. The model receives bounded history from that conversation. The same request UUID and content replay the saved result, including failures, without another model call. Different content with the same ID returns 409. One response may generate per conversation at a time.

Model calls happen outside database transactions. Transport, malformed-response, and timeout failures become saved error replies. Requests left processing after `OLLAMA_REQUEST_TIMEOUT + 60` seconds are marked failed when the conversation is accessed again. Stopping receipt in the browser does not stop generation; its eventual result is saved. Distributed durable execution remains a subsequent task.

## Saved task plans

Open **Task plans** in the sidebar, enter a project request, and choose **Generate task plan**. The explicitly configured master receives the request and a bounded snapshot of that project's name, requirements, and summary. It does not receive other project histories or endpoint credentials in its prompt. The transport uses the saved master endpoint and key, just as chat does. Ollama must support the JSON schema `format` parameter. No additional Python dependencies are required.

```http
POST /api/projects/1/plans/
Content-Type: application/json

{
  "id": "157b1067-0b98-48f5-8229-449507d22403",
  "request": "Build a login system with Django and PostgreSQL."
}
```

The request is limited to 8,000 characters. Project context includes up to 6,000 requirements characters and 2,000 summary characters. The master must return a title, summary, and 1-20 tasks. Each task has a unique local `key`, title, specialization `type`, instructions, `requiredCapabilities`, `expectedOutput`, `acceptanceCriteria`, and `dependsOn` keys referencing only tasks in that plan. All fields, lengths, list types, and specializations are checked server-side. Unknown fields, duplicate keys/dependencies, missing references, self-dependencies, and cycles are rejected. Tasks are persisted in dependency order within a single transaction and start `PENDING`, with no assigned agent. Existing tasks are retained.

Plans have `GENERATING`, `READY` (saved), or `FAILED` status. POST returns 201 for a newly saved plan, 200 for a completed replay, 202 for an in-flight replay, or 502 with the saved failure under `data`. Reusing the same UUID with different text or another project returns 409. A failed UUID replays its failure; use a new UUID to request another generation. One plan may generate per project at a time. Generation calls occur outside database transactions. Generation exceeding `OLLAMA_REQUEST_TIMEOUT + 60` seconds is marked failed on the next history/detail/submission access, and late output is rejected. Project archival during generation also prevents task creation. Database failures roll back task and dependency writes; an unfinished reservation can then expire normally.

GET history returns `items` (20 per page, newest first) and `nextOffset`; pass `offset` to retrieve older plans. GET detail includes the source `request`, model/agent snapshot, summary, safe failure details, task count, and task requirements and dependencies. Both routes require an active project and the same workspace access rules as other APIs. POST requires CSRF. Generated plan tasks and dependencies are read-only in admin; legacy task dependency edits also reject cross-project links and cycles.

The planning dialog reloads saved history, displays generation failures, and polls in-flight attempts after a browser reload. Closing the dialog stops polling, not server generation. This stage is synchronous generation with persistent attempt recovery; it does not provide a background scheduler, automatic agent routing, durable execution, repository changes, or test execution. A master process failure requires a new generation after the old attempt expires. Larger plans can be handled as separate requests; automatic recursive decomposition is not implemented.

## Browser migration

**Import browser data** appears when legacy `multiagent_state_v1` records exist. The import is atomic, preserves an existing master, remaps endpoint references, and enforces the five-specialist limit. Repeating its ID and content reuses the result; changed content returns 409. Old browser data is removed only after success; edits made during import are retained.

Only appearance, current selection, and the import retry ID remain under `multiagent_preferences_v1`. Database failures are displayed; no automatic browser-storage or SQLite fallback occurs.

## Verification

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test django_modules --noinput
node --check django_modules/static/workspace/workspace.js
```

Tests cover independent clients, CSRF/access controls, write-only keys, CRUD/search/pagination, idempotent requests/imports, failed and interrupted responses, PostgreSQL concurrency limits, and migration preservation. The row-lock test intentionally runs only on PostgreSQL. Python functions have brief docstrings; JavaScript functions and callbacks have short explanations.
