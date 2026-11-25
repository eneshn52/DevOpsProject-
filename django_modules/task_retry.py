"""FR-23: failed tasks are retried and reassigned.

Rules:
  1. A failed attempt puts the task back in the queue (PENDING, no agent) while it has
     attempts left. settings.TASK_MAX_ATTEMPTS (default 3) is the limit; at the limit the
     task stays FAILED.
  2. The scheduler does not give a task back to an agent that already failed it (FR-14 rule 3),
     so the retry goes to another agent. When no other agent can take it, the agents that
     failed may try again (retry_exclusions), with a different seed and the failure reason
     in the prompt (retry_note).
  3. A task held by a worker that went offline is taken back (release_abandoned_tasks):
     not started -> back in the queue; started and past its deadline -> a failed attempt.

Benchmark tasks (FR-9) are never retried: a failed benchmark case is a measurement.
"""
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .agent_profiles import refresh_profile
from .models import Agent, Metric, Task, WorkerTaskResult
from .scheduler import is_eligible
from .task_results import fail_conflicts_for_task

OFFLINE_ERROR = 'Worker went offline before returning a result.'
NOTE_LIMIT = 300  # characters of the previous failure reason shown to the next attempt


def max_attempts():
    """How many times a task may be attempted in total (at least 1)."""
    return max(1, settings.TASK_MAX_ATTEMPTS)


def can_retry(attempts, limit):
    """True while a task that has used `attempts` attempts may be tried again."""
    return attempts < limit


def retry_failed_task(task, attempt):
    """Put a FAILED task back in the queue if it has attempts left. Returns True when re-queued.

    attempt: the attempt that just failed. The update only matches while the task is still
    FAILED on that attempt, so a late or repeated call cannot disturb a newer attempt.
    """
    if not can_retry(attempt, max_attempts()) or hasattr(task, 'benchmark_case'):
        return False
    requeued = bool(Task.objects.filter(pk=task.pk, status='FAILED', attempts=attempt)
                    .update(status='PENDING', assigned_agent=None))
    if requeued:
        task.status, task.assigned_agent = 'PENDING', None
    return requeued


def after_failure(task, attempt):
    """Call once a task attempt is recorded as FAILED: retry it, or make the failure final."""
    if retry_failed_task(task, attempt):
        return True
    fail_conflicts_for_task(task)  # FR-24: a resolver that is out of attempts fails its conflict
    return False


def retry_exclusions(tasks, candidates, now, scores, failed):
    """{task_pk: agent pks that must not take the task}, for the scheduler's `failed` argument.

    Normally these are the agents that already failed the task, so a retry is reassigned.
    If that leaves no agent able to take the task, the exclusion is dropped: retrying on the
    same agent is better than a task that waits forever. Pure function, no queries.
    """
    exclusions = {}
    for task in tasks:
        excluded = failed.get(task.pk, set())
        if excluded and not any(candidate.pk in scores and is_eligible(candidate, task, now, excluded)
                                for candidate in candidates):
            excluded = set()
        exclusions[task.pk] = excluded
    return exclusions


def retry_note(task):
    """Prompt text for a retry: why the previous attempt failed. Empty on the first attempt."""
    if task.attempts <= 1:
        return ''
    previous = (WorkerTaskResult.objects.filter(task=task, status='FAILED', attempt__lt=task.attempts)
                .exclude(error='').order_by('-attempt').first())
    if not previous:
        return ''
    return ('\nA previous attempt at this task failed. Reason: '
            f'{previous.error[:NOTE_LIMIT]}\nAvoid that problem in this attempt.')


def retry_seed(task, base=42):
    """Attempt 1 keeps the fixed seed; each retry gets another so the same model can answer differently."""
    return base + max(0, task.attempts - 1)


def fail_execution(execution, error, agent=None):
    """Mark one RUNNING execution FAILED and apply the retry rule. Returns False if it was already final.

    agent: the caller's (locked) Agent instance when it has one, so its fields stay in step.
    """
    if not WorkerTaskResult.objects.filter(pk=execution.pk, status='RUNNING').update(
            status='FAILED', error=error, completed_at=timezone.now()):
        return False
    execution.refresh_from_db()
    agent, task = agent or execution.agent, execution.task
    failed = Task.objects.filter(pk=task.pk, assigned_agent=agent, attempts=execution.attempt,
                                 status='IN_PROGRESS').update(status='FAILED')
    Metric.objects.create(task=task, agent=agent, model=execution.model_name, success=False,
                          duration_s=(execution.completed_at - execution.started_at).total_seconds())
    from .benchmarking import record_case_result
    record_case_result(task, execution)
    if failed:
        after_failure(task, execution.attempt)
    refresh_profile(agent)  # FR-10: a timed-out attempt counts as a failure
    if agent.current_task_id == task.pk:
        agent.current_task = None
        if agent.status != 'OFFLINE':
            agent.status = 'IDLE'
        agent.save(update_fields=['current_task', 'status'])
    return True


def release_abandoned_tasks(now):
    """Take back the tasks of workers that went offline. Returns how many tasks were released.

    Runs when any worker asks for work, so a dead worker cannot hold a task forever.
    """
    offline = (Q(assigned_agent__status='OFFLINE') | Q(assigned_agent__last_heartbeat__isnull=True)
               | Q(assigned_agent__last_heartbeat__lte=Agent.heartbeat_cutoff(now)))
    released = 0

    # Started, and the deadline passed while the worker was away: a failed attempt.
    expired = (WorkerTaskResult.objects.filter(status='RUNNING', deadline__lt=now)
               .filter(Q(agent__status='OFFLINE') | Q(agent__last_heartbeat__isnull=True)
                       | Q(agent__last_heartbeat__lte=Agent.heartbeat_cutoff(now)))
               .select_related('task', 'agent'))
    for execution in expired:
        released += fail_execution(execution, OFFLINE_ERROR)

    # Claimed but never started: nothing was attempted, so it simply goes back in the queue.
    abandoned = (Task.objects.filter(status='ASSIGNED', benchmark_case__isnull=True).filter(offline)
                 .exclude(worker_results__status='RUNNING'))
    for task in abandoned:
        still_held = Task.objects.filter(pk=task.pk, status='ASSIGNED', assigned_agent_id=task.assigned_agent_id)
        if can_retry(task.attempts, max_attempts()):
            released += still_held.update(status='PENDING', assigned_agent=None)
        elif still_held.update(status='FAILED'):
            fail_conflicts_for_task(task)
            released += 1
    return released
