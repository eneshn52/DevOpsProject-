"""Worker heartbeats and the freshness rule that marks silent agents offline (FR-6)."""
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Agent, Task
from .workspace_api import APIError, agent_json, boolean, endpoint, payload, text

HEARTBEAT_FIELDS = {'alive', 'status', 'currentTaskId'}
WORKER_STATUSES = {'IDLE', 'BUSY'}


def heartbeat_json(agent):
    """Serialize an agent with its reported task and the expected heartbeat cadence."""
    return {**agent_json(agent), 'currentTaskId': str(agent.current_task_id) if agent.current_task_id else None,
            'heartbeatInterval': settings.AGENT_HEARTBEAT_INTERVAL, 'heartbeatTimeout': settings.AGENT_HEARTBEAT_TIMEOUT}


def reported_task(data, status):
    """Validate the task a worker reports and require one only while busy."""
    value = data.get('currentTaskId')
    if value in (None, ''):
        if status == 'BUSY':
            raise APIError('A busy agent must report its currentTaskId.')
        return None
    if status != 'BUSY':
        raise APIError('Only a busy agent can report a currentTaskId.')
    if isinstance(value, bool) or not str(value).isdigit():
        raise APIError('Enter a valid currentTaskId.')
    try:
        return Task.objects.get(pk=int(value))
    except Task.DoesNotExist:
        raise APIError('The reported task was not found.', 404, 'TASK_NOT_FOUND') from None


@csrf_exempt
@endpoint('POST')
def heartbeat(request, agent_id):
    """Record that a specialist is alive, idle or busy, and which task it is running."""
    data = payload(request, HEARTBEAT_FIELDS, max_bytes=4096)
    alive = boolean(data, 'alive', True)
    status = text(data, 'status', 'IDLE', 20).upper() if alive else 'OFFLINE'
    if alive and status not in WORKER_STATUSES:
        raise APIError('status must be IDLE or BUSY.')
    task = reported_task(data, status) if alive else None
    with transaction.atomic():
        agent = Agent.objects.exclude(role='MASTER').select_for_update().get(pk=agent_id)
        agent.status, agent.current_task, agent.last_heartbeat = status, task, timezone.now()
        agent.save(update_fields=['status', 'current_task', 'last_heartbeat'])
    return heartbeat_json(agent)
