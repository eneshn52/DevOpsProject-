"""Task distribution: GET /api/agents/{id}/next-task.

An idle worker asks the master for work. The master loads the current state from
the database, lets the FR-14 scheduler (scheduler.pick_task_for) decide, and claims
the chosen task for that agent. Answers:
  200 + the task as JSON   the agent should run this task
  204 (empty)              nothing for this agent right now; ask again later
  409 AGENT_OFFLINE        the agent must send a heartbeat first
"""
from functools import wraps

from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q
from django.http import HttpResponse
from django.utils import timezone

from .capabilities import get_scores
from .models import Agent, Metric, Task, TaskDependency
from .scheduler import DEFAULT_MAX_TOKENS, pick_task_for
from .task_weights import TASK_WEIGHTS
from .workspace_api import APIError, endpoint

ACTIVE_STATUSES = ('ASSIGNED', 'IN_PROGRESS')  # tasks an agent holds right now
CLAIM_ATTEMPTS = 3                             # retries when another agent wins the race for a task


def task_json(task):
    """What the worker needs to run a task."""
    return {'id': str(task.pk), 'projectId': str(task.project_id) if task.project_id else None,
            'type': task.type, 'title': task.title, 'instructions': task.instructions,
            'expectedOutput': task.expected_output, 'acceptanceCriteria': task.acceptance_criteria,
            'requiredCapabilities': task.required_capabilities,
            'maxTokens': task.max_tokens or DEFAULT_MAX_TOKENS, 'attempt': task.attempts}


def ready_tasks():
    """PENDING tasks whose dependencies are all COMPLETED, oldest first.

    Tasks in archived projects or in plans that are not saved (still generating or failed) are left out.
    """
    unfinished_dependency = (TaskDependency.objects.filter(task=OuterRef('pk'))
                             .exclude(depends_on__status='COMPLETED'))
    return list(Task.objects.filter(status='PENDING')
                .filter(Q(project__isnull=True) | Q(project__archived_at__isnull=True))
                .filter(Q(plan__isnull=True) | Q(plan__status='READY'))
                .exclude(Exists(unfinished_dependency))
                .order_by('created_at', 'pk'))


def queued_counts():
    """{agent_pk: number of tasks the agent holds (assigned or in progress)}."""
    rows = (Task.objects.filter(status__in=ACTIVE_STATUSES, assigned_agent__isnull=False)
            .values('assigned_agent').annotate(count=Count('pk')))
    return {row['assigned_agent']: row['count'] for row in rows}


def failed_agents(task_ids):
    """{task_pk: set of agent pks that already failed it}, from Metric rows with success=False."""
    failed = {}
    rows = (Metric.objects.filter(task_id__in=task_ids, success=False, agent__isnull=False)
            .values_list('task_id', 'agent_id'))
    for task_id, agent_id in rows:
        failed.setdefault(task_id, set()).add(agent_id)
    return failed


def choose_task(agent, now):
    """Load the scheduler's inputs from the database and ask it which task this agent should take."""
    tasks = ready_tasks()
    if not tasks:
        return None
    candidates = list(Agent.objects.exclude(role='MASTER'))
    return pick_task_for(agent, tasks, candidates, now,
                         scores=get_scores([c.pk for c in candidates]),
                         queued=queued_counts(),
                         failed=failed_agents([t.pk for t in tasks]),
                         weights=TASK_WEIGHTS)


def no_content(view):
    """Turn the API's (None, 204) answer into a real empty 204 response."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        if response.status_code == 204:
            response = HttpResponse(status=204)
            response['Cache-Control'] = 'no-store'
        return response
    return wrapped


@no_content
@endpoint('GET')
def next_task(request, agent_id):
    """Give the asking worker its next task, or 204 when there is nothing for it."""
    now = timezone.now()
    with transaction.atomic():
        # Lock this agent's row so two requests from the same worker are handled one at a time.
        agent = Agent.objects.exclude(role='MASTER').select_for_update().get(pk=agent_id)
        if not agent.is_online(now):
            raise APIError('Send a heartbeat before asking for work.', 409, 'AGENT_OFFLINE')

        # A worker runs one task at a time. If it already holds one (for example because the
        # previous answer got lost on the network), give the same task back instead of a new one.
        held = (Task.objects.filter(assigned_agent=agent, status__in=ACTIVE_STATUSES)
                .order_by('created_at', 'pk').first())
        if held:
            return task_json(held)

        for _ in range(CLAIM_ATTEMPTS):
            task = choose_task(agent, now)
            if task is None:
                return None, 204
            # Claim only if the task is still PENDING. This single UPDATE is atomic in the database,
            # so if another worker claimed it a moment ago, 0 rows change and we choose again.
            claimed = (Task.objects.filter(pk=task.pk, status='PENDING')
                       .update(status='ASSIGNED', assigned_agent=agent, attempts=F('attempts') + 1))
            if claimed:
                task.refresh_from_db()
                return task_json(task)
        return None, 204
