# FR-23: failed tasks are retried and reassigned

- [x] A failed attempt puts the task back in the queue while it has attempts left.
- [x] The retry goes to an agent that has not failed the task; if nobody else can take it, the same agent may try again.
- [x] A retry is told why the last attempt failed and uses a different seed.
- [x] Tasks held by a worker that went offline are taken back and reassigned.
- [x] After `TASK_MAX_ATTEMPTS` attempts (default 3) the task stays `FAILED`.

Code: `django_modules/task_retry.py`. Tests: `django_modules/test_task_retry.py`.

## What counts as a failed attempt

| Failure | Where it is detected |
|---|---|
| The worker reports `FAILED` (model error) | `task_execution.result` |
| The answer is not the agreed JSON, or breaks the folder rules (FR-18) | `task_execution.result` |
| Automated tests or the review reject the output (FR-21, FR-22) | `task_execution.result` |
| The execution deadline passes | `task_execution.expire_executions` |
| The worker goes offline while running and the deadline passes | `task_retry.release_abandoned_tasks` |

Each of these ends in `task_retry.after_failure(task, attempt)`.

## The rules

1. **Retry.** If `attempt < TASK_MAX_ATTEMPTS`, the task becomes `PENDING` with no agent, so `ready_tasks()` offers it again.
   Otherwise it stays `FAILED`. Set the limit with the `TASK_MAX_ATTEMPTS` environment variable; `1` turns retries off.
2. **Reassign.** FR-14 already refuses to give a task to an agent that failed it (a `Metric` row with `success=False`),
   so the retry goes to the next best agent. `retry_exclusions()` drops that rule for a task when no other online,
   benchmarked agent can take it, because a retry on the same agent is better than a task that waits forever.
3. **A different attempt.** Workers run with temperature 0 and a fixed seed, so repeating the same prompt would repeat the
   same answer. From attempt 2 on, the prompt ends with the previous failure reason (`retry_note`, at most 300 characters)
   and the seed is `42 + attempt - 1` (`retry_seed`).
4. **Offline workers.** Whenever a worker asks for work, `release_abandoned_tasks()` looks for tasks held by workers whose
   heartbeat has timed out. A task that was claimed but not started goes straight back in the queue and nothing is recorded
   against the worker. A task that was started is left until its deadline, then recorded as a failed attempt.

## What does not change

- FR-9 benchmark tasks are never retried: a failed benchmark case is a measurement.
- A late result from the previous worker is still rejected with 409 (`STALE_ASSIGNMENT`).
- Sending the same final result twice still returns the same answer; `result` now checks for this before the assignment
  check, because the task may already be re-queued.
- FR-24: a conflict is marked `FAILED` only when its resolver task is out of attempts.
- Every attempt keeps its own `WorkerTaskResult` and `Metric` row, so FR-10 profiles count each failed attempt.

## Not covered

- Tasks run through the standalone Python test runner (`?runner=python`, `test_result` endpoint) are not retried.
- Tasks that depend on a task that failed for good stay `PENDING`; there is no "blocked" status yet.
- There is no button to retry a task by hand after its last attempt.
