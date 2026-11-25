# Local integration acceptance

Run PowerShell from the repository root with the existing virtual environment active. Use a new database for this run. This keeps the previous demo and workspace data available. The local settings disable writes to the generated-projects Git repository.

## Repeat the automated checks

```powershell
$env:USE_SQLITE = 'True'
$env:FR21_MODEL = Read-Host 'Installed Ollama coding model tag (see ollama list)'
$env:FR21_OLLAMA_URL = 'http://127.0.0.1:11434'
$scratch = Join-Path $env:TEMP ('fr22-tests-' + [guid]::NewGuid())
python -m pytest -q -rs -p no:cacheprovider --basetemp $scratch
python -m unittest discover -s benchmarks/model_test -p 'test_*.py'
```

Start Ollama on loopback first if it is stopped. The suite starts its own Django master. It exercises heartbeat, ordinary leased execution, standalone Python testing/review, combined project execution, result replay, rejected results, profiles and FR-18 writes in temporary Git repositories. The real-model cases use FR21_MODEL. PostgreSQL locking tests skip on SQLite.

## Run one project task manually using both flags

Terminal A, using a fresh SQLite file:

```powershell
$env:DJANGO_SETTINGS_MODULE = 'django_settings.fr21_local'
$env:FR21_DB_PATH = Join-Path $env:TEMP ('fr22-demo-' + [guid]::NewGuid() + '.sqlite3')
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --noinput
python manage.py showmigrations django_modules
$path = python manage.py fr21_demo
$agentId = $path.Split('/')[3]
$env:FR21_MODEL = Read-Host 'Installed Ollama model tag'
python manage.py shell -c "import os; from django_modules.models import Agent,AIModel,Task,Project; a=Agent.objects.get(name='fr21-local'); a.ai_model=AIModel.objects.get_or_create(ollama_name=os.environ['FR21_MODEL'])[0]; a.save(update_fields=['ai_model']); Task.objects.filter(status='PENDING').update(project=Project.objects.create(name='Local integration'),type='BACKEND')"
$token = python -c "import secrets; print(secrets.token_urlsafe(32))"
$tokens = @{}
$tokens[$agentId] = $token
$env:FR16_WORKER_TOKENS = $tokens | ConvertTo-Json -Compress
$path
$token
python manage.py runserver 127.0.0.1:8002 --noreload
```

Terminal B, same repository and virtual environment:

```powershell
$path = Read-Host 'Paste the /api/agents/.../test-result/ path from A'
$agentId = $path.Split('/')[3]
$env:FR16_WORKER_TOKEN = Read-Host 'Paste the disposable local token from A'
python worker/worker.py --server-url http://127.0.0.1:8002 --agent-id $agentId --execute --run-tests --once
(Invoke-RestMethod "http://127.0.0.1:8002$path").data | ConvertTo-Json -Depth 8
```

Check for passing cases and approved review. The signed job specifies the generator model. Optionally add `--review-model <installed-tag>`. A different model answer can legitimately fail. For a repeatable passing and rejecting project pair with deterministic model responses, run `python -m pytest -q django_modules/test_task_execution.py -k worker_fetches` with USE_SQLITE=True. To exercise standalone mode, use the separate unauthenticated loopback demo in FR-22.md (its agent should not have an FR-16 token). Run one worker process per agent ID.

## Result ownership

| Invocation | Task contract | Delivery |
| --- | --- | --- |
| No execution flags | Heartbeat only | Existing heartbeat API |
| `--execute` | Ordinary tasks without test_cases | Signed lease, durable outbox, one result POST, FR-18 artifact processing for project tasks |
| `--run-tests` | Standalone Python tasks with test_cases | Existing test-result POST containing source, tests and review |
| Both flags | Ordinary tasks plus opted-in Python tasks | One signed lease and one result payload per attempt. For opted-in tasks, test and review the generated output before delivery. |

Combined project tasks currently require one Python file implementing solve(value). The master applies FR-18 path and format validation and checks the report's source against the exact artifact content before publishing. A failed or missing approval fails the attempt. Successful combined tasks keep FR-16 metrics/profile updates and the standard FR-18/20 artifact callbacks. The standalone endpoint cannot complete leased executions or project tasks. It remains available for older standalone clients and report reads. Decide as a team whether to retire standalone submission, and how to select entry points and test multi-file artifacts, before extending this contract.

## Migration history

FR-21's original 0008_task_code_tests is already applied in the previous local demo. Main independently progressed from 0008_task_capability_weights through 0012_agent_profile, 0013_task_worker_context, and 0014_fr24_task_output_conflicts. Migration 0015_merge_task_code_tests depends on both leaves and adds no columns itself. FR-22 added no migration. Use normal migrate on a backed-up database after reviewing the plan. Do not rename the recorded 0008, delete the database, or fake either branch.
