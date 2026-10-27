"""FR-16 endpoints attached to the existing workspace and FR-14 task distributor."""
import math
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Agent, Metric, Task, WorkerTaskResult
from django_modules.workspace_api import APIError, payload

# FR-16-only access boundary. Existing view implementations remain unchanged.
import hashlib
import hmac
from functools import wraps
from types import SimpleNamespace
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError
from django.http import JsonResponse


def for_worker(strict=False):
    """Keep legacy workspace access, or authenticate this agent's shared token."""
    def decorate(view):
        @wraps(view)
        def wrapped(request, agent_id, **kwargs):
            token = settings.FR16_WORKER_TOKENS.get(str(agent_id), '')
            supplied = request.headers.get('Authorization', '')
            if not token and not strict and not supplied.startswith('Bearer '):
                return view(request, agent_id=agent_id, **kwargs)
            if not isinstance(token, str) or not token or not hmac.compare_digest(
                    supplied.encode(), ('Bearer ' + token).encode()):
                return JsonResponse({'error': 'A valid shared token for this worker is required.'}, status=403)
            if view.__name__ == 'heartbeat' and request.method == 'POST':
                try:
                    body = json.loads(request.body)
                    task_id = body.get('currentTaskId') if isinstance(body, dict) else None
                    if task_id is not None and (isinstance(task_id, bool) or not str(task_id).isdigit() or
                            not Task.objects.filter(pk=int(task_id), assigned_agent_id=agent_id, status='IN_PROGRESS').exists()):
                        return JsonResponse({'error': 'Cannot report another agent task.'}, status=403)
                except (ValueError, UnicodeDecodeError):
                    return JsonResponse({'error': 'Invalid JSON.'}, status=400)
            user = request.user
            try:
                # This request-scoped principal only authorizes the already-validated
                # worker for the bound heartbeat/next-task operation, never the registry/admin.
                request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
                if view.__name__ == 'next_task':
                    with transaction.atomic():
                        try:
                            agent = Agent.objects.select_for_update().exclude(role='MASTER').get(pk=agent_id)
                        except Agent.DoesNotExist:
                            return JsonResponse({'error': 'Worker not found.'}, status=404)
                        expire_executions(agent)
                        return view(request, agent_id=agent_id, **kwargs)
                return view(request, agent_id=agent_id, **kwargs)
            finally:
                request.user = user
        return wrapped
    return decorate


def sign_job(data, agent_id):
    raw = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
    signature = hmac.new(settings.FR16_WORKER_TOKENS[str(agent_id)].encode(), raw, hashlib.sha256).hexdigest()
    return {**data, 'signature': signature}


def endpoint(*methods):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                response = JsonResponse({'error': 'Method not allowed.'}, status=405)
                response['Allow'] = ', '.join(methods)
                return response
            try:
                return JsonResponse({'data': view(request, *args, **kwargs)})
            except APIError as error:
                return JsonResponse({'error': str(error), 'code': error.code}, status=error.status)
            except ObjectDoesNotExist:
                return JsonResponse({'error': 'Record not found.'}, status=404)
            except (ValidationError, ValueError):
                return JsonResponse({'error': 'Invalid request data.'}, status=400)
            except IntegrityError:
                return JsonResponse({'error': 'Concurrent execution conflict.'}, status=409)
        return wrapped
    return decorate



def expire_executions(agent):
    """Caller holds the agent lock. Expiration is checked on worker requests."""
    for execution in WorkerTaskResult.objects.filter(agent=agent, status='RUNNING', deadline__lt=timezone.now()):
        execution.status, execution.error = 'FAILED', 'Execution deadline exceeded.'
        execution.completed_at = timezone.now()
        execution.save(update_fields=['status', 'error', 'completed_at'])
        Task.objects.filter(pk=execution.task_id, assigned_agent=agent, attempts=execution.attempt, status='IN_PROGRESS').update(status='FAILED')
        Metric.objects.create(task=execution.task, agent=agent, model=execution.model_name, success=False,
                              duration_s=(execution.completed_at - execution.started_at).total_seconds())
        if agent.current_task_id == execution.task_id:
            agent.current_task, agent.status = None, 'IDLE'
            agent.save(update_fields=['current_task', 'status'])


def job_json(execution):
    return sign_job({'task_id': str(execution.task_id), 'attempt': execution.attempt, 'lease': str(execution.lease),
            'model': execution.model_name, 'prompt': execution.prompt, 'options': execution.options,
            'context_window': execution.context_window,
            'timeout_s': max(1, int((execution.deadline - timezone.now()).total_seconds()))}, execution.agent_id)


@csrf_exempt
@for_worker(strict=True)
@endpoint('POST')
def start(request, agent_id, task_id):
    """Reserve execution only for a task already assigned by the existing API."""
    payload(request, set())
    with transaction.atomic():
        agent = Agent.objects.select_for_update().exclude(role='MASTER').get(pk=agent_id)
        expire_executions(agent)
        task = Task.objects.select_for_update().get(pk=task_id)
        if task.assigned_agent_id != agent.pk:
            raise APIError('This task belongs to another worker.', 403, 'WRONG_WORKER')
        if task.status not in {'ASSIGNED', 'IN_PROGRESS'}:
            raise APIError('Task must be assigned before execution.', 409, 'TASK_NOT_ASSIGNED')
        if task.dependencies.exclude(depends_on__status='COMPLETED').exists():
            raise APIError('Task dependencies are not complete.', 409, 'DEPENDENCIES_PENDING')
        previous = WorkerTaskResult.objects.filter(task=task, attempt=task.attempts).first()
        if previous:
            if previous.agent_id != agent.pk or previous.status != 'RUNNING':
                raise APIError('This attempt is already final.', 409, 'ATTEMPT_FINAL')
            return job_json(previous)
        if WorkerTaskResult.objects.filter(agent=agent, status='RUNNING').exists():
            raise APIError('Worker already has an active execution.', 409, 'WORKER_BUSY')
        if not agent.ai_model:
            raise APIError('Configure the worker model first.', 409, 'MODEL_REQUIRED')
        context = agent.context_window or agent.ai_model.max_context or 4096
        prompt = f'Task: {task.title}\nInstructions:\n{task.instructions}\nExpected output:\n{task.expected_output}'
        if task.acceptance_criteria:
            prompt += '\nAcceptance criteria:\n' + '\n'.join(map(str, task.acceptance_criteria))
        # Conservative byte upper bound rather than assuming one character = one token.
        if context < 512 or len(prompt.encode()) + 256 > int(context * .8):
            raise APIError('Task exceeds the configured context budget.', 409, 'CONTEXT_LIMIT')
        if task.attempts == 0:
            task.attempts = 1
        execution = WorkerTaskResult.objects.create(task=task, agent=agent, attempt=task.attempts,
            model_name=agent.ai_model.ollama_name, prompt=prompt, context_window=context,
            options={'temperature': 0, 'seed': 42, 'num_ctx': context,
                     'num_predict': min(task.max_tokens or 1024, context // 5)},
            deadline=timezone.now() + timedelta(seconds=settings.FR16_TASK_TIMEOUT))
        task.status = 'IN_PROGRESS'
        task.save(update_fields=['status', 'attempts'])
        agent.status, agent.current_task = 'BUSY', task
        agent.save(update_fields=['status', 'current_task'])
        return job_json(execution)


def validate(data):
    metadata = data.get('model_metadata', {})
    if not isinstance(metadata, dict) or len(json.dumps(metadata)) > 16000:
        raise APIError('Invalid model metadata.')
    if data.get('status') == 'COMPLETED' and (not isinstance(metadata.get('digest'), str) or not metadata['digest']):
        raise APIError('Completed result requires the local model digest.')
    if data.get('status') not in {'COMPLETED', 'FAILED'}:
        raise APIError('Invalid execution status.')
    if type(data.get('attempt')) is not int or data['attempt'] < 1:
        raise APIError('Invalid attempt.')
    for name in ('output', 'error'):
        if not isinstance(data.get(name), str) or len(data[name]) > 100000:
            raise APIError(f'Invalid {name}.')
    if data['status'] == 'COMPLETED' and (not data['output'].strip() or data['error']):
        raise APIError('Successful execution requires output and no error.')
    if data['status'] == 'FAILED' and not data['error'].strip():
        raise APIError('Failed execution requires an error.')
    for name in ('duration_s',):
        value = data.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 86400:
            raise APIError(f'Invalid {name}.')
    for name in ('prompt_tokens', 'output_tokens'):
        if type(data.get(name)) is not int or not 0 <= data[name] <= 10000000:
            raise APIError(f'Invalid {name}.')
    if not isinstance(data.get('lease'), str) or len(data['lease']) > 40:
        raise APIError('Invalid execution lease.')


@csrf_exempt
@for_worker(strict=True)
@endpoint('POST')
def result(request, agent_id, task_id):
    fields = {'attempt', 'lease', 'status', 'output', 'error', 'duration_s', 'prompt_tokens', 'output_tokens', 'model_metadata'}
    data = payload(request, fields, max_bytes=700000)
    validate(data)
    with transaction.atomic():
        agent = Agent.objects.select_for_update().exclude(role='MASTER').get(pk=agent_id)
        task = Task.objects.select_for_update().get(pk=task_id)
        if task.assigned_agent_id != agent.pk or task.attempts != data['attempt']:
            raise APIError('Task assignment or attempt changed.', 409, 'STALE_ASSIGNMENT')
        execution = WorkerTaskResult.objects.select_for_update().get(task=task, agent=agent, attempt=data['attempt'])
        if str(execution.lease) != data['lease']:
            raise APIError('Execution lease does not match.', 409, 'STALE_LEASE')
        if execution.status != 'RUNNING':
            if execution.result != data:
                raise APIError('A final result already exists.', 409, 'RESULT_CONFLICT')
            return {'status': execution.status, 'id': execution.pk}
        if task.status != 'IN_PROGRESS':
            raise APIError('Task is no longer running.', 409, 'STALE_TASK_STATE')
        expire_executions(agent)
        execution.refresh_from_db()
        if execution.status != 'RUNNING':
            return {'status': 'FAILED', 'error': execution.error, 'id': execution.pk}
        execution.result, execution.output, execution.error = data, data['output'], data['error']
        execution.status, execution.completed_at = data['status'], timezone.now()
        execution.save(update_fields=['result', 'output', 'error', 'status', 'completed_at'])
        task.status = execution.status
        task.save(update_fields=['status'])
        Metric.objects.create(task=task, agent=agent, model=execution.model_name,
            success=data['status'] == 'COMPLETED', duration_s=data['duration_s'],
            prompt_tokens=data['prompt_tokens'], output_tokens=data['output_tokens'], retries=max(0, task.attempts - 1))
        agent.status, agent.current_task, agent.last_heartbeat = 'IDLE', None, timezone.now()
        agent.save(update_fields=['status', 'current_task', 'last_heartbeat'])
        return {'status': execution.status, 'id': execution.pk}


@for_worker(strict=True)
@endpoint('GET')
def profile(request, agent_id):
    agent = Agent.objects.exclude(role='MASTER').select_related('ai_model').get(pk=agent_id)
    return {'id': str(agent.pk), 'name': agent.name,
            'modelName': agent.ai_model.ollama_name if agent.ai_model else ''}
