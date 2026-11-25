# FR-10: agent profiles updated from real task outcomes

- [x] Keep a profile per specialist: tasks completed and failed, success rate, average time spent, output speed and total time.
- [x] Rebuild the profile from the FR-16 `Metric` rows after every task result, including timed-out attempts.
- [x] Leave FR-9 benchmark tasks out, because they test the agent rather than measure real work.
- [x] Feed the track record into FR-14 agent selection without overwriting the benchmark scores.
- [x] Show the profile in the workspace API, the specialist panel and the admin.
- [x] Backfill command for outcomes recorded before FR-10.

## How a profile is built

Every finished attempt already leaves a `Metric` row (success, `duration_s`, tokens). After each one,
`agent_profiles.refresh_profile(agent)` recalculates the agent's `AgentProfile` from those rows:

| Field | Meaning |
|---|---|
| `tasks_completed`, `tasks_failed` | All real outcomes of this agent |
| `success_rate` | Share of successful outcomes among the last 50 (`RECENT_WINDOW`) |
| `avg_duration_s` | Average time spent per attempt over the last 50 |
| `tokens_per_second` | Median output speed of recent successful tasks with at least 32 output tokens |
| `total_time_s` | Time spent on all real tasks |
| `last_outcome_at` | When the latest attempt finished |

The profile is always recalculated from the data, so calling the refresh twice is harmless and the profile cannot drift.

## How it changes agent selection

`task_distribution.choose_task` passes the benchmark scores through `agent_profiles.adjusted_scores()` before FR-14 scores the agents:

- **coding** and **reasoning** are blended with `success_rate x 100`
- **speed** is blended with the real tokens per second, on FR-9's scale (20 tok/s = 100)
- **context** is unchanged, because task outcomes do not measure it

```
weight   = min(0.5, outcomes / (outcomes + 10))
adjusted = (1 - weight) x benchmark + weight x observed
```

So a new agent is judged only by its benchmark, one bad task moves its score by under 10%, and real results never count for more than half.
Benchmark scores in `AgentCapabilityScore` are not modified, so a new FR-9 benchmark still sets the baseline. Agents without a benchmark still get no tasks.

Example: an agent that benchmarks coding 95 but failed its last 10 tasks drops to 47.5, and an agent at 90 that succeeded on all 10 goes up to 95, so the reliable agent gets the next backend task.

## Files

| File | Change |
|---|---|
| `django_modules/agent_profiles.py` | New: refresh, blend and API helpers |
| `django_modules/models.py` | New `AgentProfile` model |
| `django_modules/migrations/0012_agent_profile.py` | New table |
| `django_modules/task_execution.py` | Refresh the profile after a result and after a timeout |
| `django_modules/task_distribution.py` | Scheduler uses `adjusted_scores()` |
| `django_modules/workspace_api.py` | `profile` in agent JSON |
| `django_modules/static/workspace/workspace.js`, `workspace.css` | Track record line under each specialist, also in the status tooltip |
| `django_modules/admin.py` | Read-only `AgentProfile` admin |
| `django_modules/management/commands/rebuild_agent_profiles.py` | Backfill command |
| `django_modules/test_agent_profiles.py` | 22 tests |

## After merging

```
python manage.py migrate
python manage.py rebuild_agent_profiles
```

Validation: 265 tests passed, 68 subtests passed on PostgreSQL (243 existing + 22 new); 263 passed, 2 skipped on SQLite.
Django checks, migration consistency and JavaScript syntax checks passed. A live server check confirmed the page,
the agent API (`profile` is `null` until the first real task) and specialist editing.
