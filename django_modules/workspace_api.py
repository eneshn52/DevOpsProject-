"""JSON API for the single shared development workspace."""
import hashlib
import json
import logging
import uuid
from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import URLValidator
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.utils import timezone

from .models import (
    AIModel, APIConnection, Agent, ChatMessage, Conversation, Project,
    Workspace, WorkspaceImport,
)

logger = logging.getLogger(__name__)
MAX_SPECIALISTS = 5
SPECIALIST_ROLES = {value for value, _ in Agent.ROLE_CHOICES} - {'MASTER'}


class APIError(Exception):
    def __init__(self, message, status=400, code='INVALID_REQUEST'):
        """  init  ."""
        super().__init__(message)
        self.status, self.code = status, code


def endpoint(*methods):
    """Apply method checks, workspace access rules, and JSON error handling."""
    def decorate(view):
        """Attach the shared API boundary to a view."""
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            """Validate access, execute the view, and serialize its result."""
            if request.method not in methods:
                response = JsonResponse({'error': 'Method not allowed.', 'code': 'METHOD_NOT_ALLOWED'}, status=405)
                response['Allow'] = ', '.join(methods)
                return response
            local = request.META.get('REMOTE_ADDR') in {'127.0.0.1', '::1'}
            if not (request.user.is_authenticated and request.user.is_staff) and not (local and settings.WORKSPACE_ALLOW_LOCAL_ACCESS):
                return JsonResponse({'error': 'Sign in as a workspace administrator.', 'code': 'ACCESS_DENIED'}, status=403)
            try:
                result = view(request, *args, **kwargs)
                data, status = result if isinstance(result, tuple) else (result, 200)
                response = JsonResponse({'data': data}, status=status)
            except APIError as error:
                response = JsonResponse({'error': str(error), 'code': error.code}, status=error.status)
            except ValidationError as error:
                response = JsonResponse({'error': 'Check the submitted fields.', 'code': 'VALIDATION_ERROR', 'fields': error.message_dict if hasattr(error, 'message_dict') else error.messages}, status=400)
            except ObjectDoesNotExist:
                response = JsonResponse({'error': 'The requested record was not found.', 'code': 'NOT_FOUND'}, status=404)
            except (IntegrityError, ProtectedError):
                response = JsonResponse({'error': 'This change conflicts with existing records.', 'code': 'CONFLICT'}, status=409)
            except DatabaseError:
                logger.exception('Workspace database operation failed')
                response = JsonResponse({'error': 'The database is unavailable. Your changes were not saved.', 'code': 'DATABASE_UNAVAILABLE'}, status=503)
            response['Cache-Control'] = 'no-store'
            return response
        return wrapped
    return decorate


def payload(request, allowed, max_bytes=131072):
    """Parse a size-limited JSON object and reject unsupported fields."""
    if len(request.body) > max_bytes:
        raise APIError('The request is too large.', 413, 'REQUEST_TOO_LARGE')
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise APIError('Send a valid JSON object.') from None
    if not isinstance(data, dict) or data.keys() - set(allowed):
        raise APIError('The request contains unsupported fields.')
    return data


def text(data, key, default='', limit=100, required=False):
    """Read a bounded string and enforce required text fields."""
    value = data.get(key, default)
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise APIError(f'Enter a valid {key} (maximum {limit} characters).')
    return value.strip()


def boolean(data, key, default=False):
    """Read a boolean field without accepting other JSON types."""
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise APIError(f'{key} must be true or false.')
    return value


def uuid_value(value):
    """Validate and normalize a record identifier as a UUID."""
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise APIError('Enter a valid record identifier.') from None


def project_record(value, active=True):
    """Find a project and optionally require it to be active."""
    if isinstance(value, bool) or not str(value).isdigit():
        raise APIError('Choose a valid project.')
    query = Project.objects.filter(archived_at__isnull=True) if active else Project.objects.all()
    return query.get(pk=int(value))


def workspace_lock():
    """Lock the singleton workspace row during configuration changes."""
    return Workspace.objects.select_for_update().get(pk=1)


def project_json(project):
    """Serialize the public fields of a project."""
    return {'id': str(project.pk), 'name': project.name, 'requirements': project.requirements_text,
            'summary': project.spec_summary, 'gitBranch': project.git_branch, 'status': project.status,
            'archived': project.archived_at is not None, 'createdAt': project.created_at.isoformat()}


def connection_json(connection):
    """Serialize endpoint configuration without exposing its API key."""
    return {'id': str(connection.pk), 'name': connection.name, 'url': connection.url,
            'hasKey': bool(connection.api_key), 'collapsed': True}


def agent_json(agent):
    """Serialize an agent and its model and endpoint references."""
    if not agent:
        return None
    return {'id': str(agent.pk), 'name': agent.name, 'modelName': agent.ai_model.ollama_name if agent.ai_model else '',
            'apiId': str(agent.api_connection_id) if agent.api_connection_id else '', 'role': agent.role,
            'machine': agent.machine, 'contextWindow': agent.context_window, 'status': agent.status,
            'online': agent.is_online(), 'lastSeenSeconds': agent.seconds_since_heartbeat(),
            'lastHeartbeat': agent.last_heartbeat.isoformat() if agent.last_heartbeat else None, 'collapsed': True}


def conversation_json(conversation):
    """Serialize conversation metadata without loading its messages."""
    return {'id': str(conversation.pk), 'projectId': str(conversation.project_id), 'title': conversation.title,
            'createdAt': conversation.created_at.isoformat(), 'updatedAt': conversation.updated_at.isoformat(),
            'deleted': conversation.deleted_at is not None}


def message_json(message):
    """Serialize a persisted user or model message for the interface."""
    return {'id': str(message.pk), 'role': 'user' if message.sender == 'user' else 'agent', 'text': message.body,
            'category': message.model_name, 'agentName': message.agent_name or 'Master',
            'error': message.is_error, 'createdAt': message.created_at.isoformat()}


def master_record():
    """Load the explicitly configured master and its endpoint."""
    return Agent.objects.select_related('ai_model', 'api_connection').filter(role='MASTER').first()


def conversation_page(request, query):
    """Return a bounded page of conversations and its next offset."""
    try:
        offset, limit = int(request.GET.get('offset', 0)), int(request.GET.get('limit', 100))
    except ValueError:
        raise APIError('Pagination values must be integers.') from None
    if offset < 0 or not 1 <= limit <= 200:
        raise APIError('Use a nonnegative offset and a limit between 1 and 200.')
    rows = list(query.order_by('-updated_at', '-id')[offset:offset + limit + 1])
    return {'items': [conversation_json(row) for row in rows[:limit]], 'nextOffset': offset + limit if len(rows) > limit else None}


@endpoint('GET')
def workspace_detail(request):
    """Load the shared workspace and the selected project history."""
    projects = list(Project.objects.filter(archived_at__isnull=True).order_by('created_at', 'pk'))
    selected = next((item for item in projects if str(item.pk) == request.GET.get('projectId')), projects[0] if projects else None)
    chats = conversation_page(request, Conversation.objects.filter(project=selected, deleted_at__isnull=True)) if selected else {'items': [], 'nextOffset': None}
    Agent.mark_stale()
    return {'name': Workspace.objects.get(pk=1).name, 'database': 'postgresql' if settings.DATABASES['default']['ENGINE'].endswith('postgresql') else 'sqlite',
            'projects': [project_json(item) for item in projects], 'projectId': str(selected.pk) if selected else None,
            'chats': chats, 'master': agent_json(master_record()),
            'models': [agent_json(item) for item in Agent.objects.exclude(role='MASTER').select_related('ai_model').order_by('pk')],
            'apis': [connection_json(item) for item in APIConnection.objects.order_by('created_at')]}


PROJECT_FIELDS = {'name', 'requirements', 'summary', 'gitBranch', 'archived'}


def update_project(project, data):
    """Validate and persist the editable project fields."""
    project.name = text(data, 'name', project.name, 200, True)
    project.requirements_text = text(data, 'requirements', project.requirements_text, 100000)
    project.spec_summary = text(data, 'summary', project.spec_summary, 20000)
    project.git_branch = text(data, 'gitBranch', project.git_branch, 100)
    if 'archived' in data:
        project.archived_at = timezone.now() if boolean(data, 'archived') else None
    project.full_clean(); project.save()
    return project_json(project)


@endpoint('GET', 'POST')
def projects(request):
    """List active projects or create a new project."""
    if request.method == 'GET':
        return [project_json(item) for item in Project.objects.filter(archived_at__isnull=True).order_by('pk')]
    return update_project(Project(), payload(request, PROJECT_FIELDS)), 201


@endpoint('GET', 'PATCH', 'DELETE')
def project_detail(request, project_id):
    """Read, edit, archive, or restore an existing project."""
    project = project_record(project_id, active=False)
    if request.method == 'GET':
        return project_json(project)
    if request.method == 'DELETE':
        project.archived_at = timezone.now(); project.save(update_fields=['archived_at'])
        return project_json(project)
    return update_project(project, payload(request, PROJECT_FIELDS))


@endpoint('GET', 'POST')
def conversations(request):
    """Search saved conversations or create one within a project."""
    if request.method == 'GET':
        query = Conversation.objects.filter(deleted_at__isnull=True, project__archived_at__isnull=True)
        if request.GET.get('projectId'):
            query = query.filter(project=project_record(request.GET['projectId']))
        search = request.GET.get('search', '').strip()
        if len(search) > 200:
            raise APIError('Search is limited to 200 characters.')
        if search:
            query = query.filter(Q(title__icontains=search) | Q(messages__body__icontains=search)).distinct()
        return conversation_page(request, query)
    data = payload(request, {'projectId', 'title', 'id'})
    project = project_record(data.get('projectId'))
    identifier = uuid_value(data['id']) if 'id' in data else uuid.uuid4()
    conversation, created = Conversation.objects.get_or_create(id=identifier, defaults={'project': project, 'title': text(data, 'title', 'New chat', 200, True)})
    if conversation.project_id != project.pk or conversation.deleted_at:
        raise APIError('This conversation identifier is already in use.', 409, 'CONFLICT')
    return conversation_json(conversation), 201 if created else 200


@endpoint('GET', 'PATCH', 'DELETE')
def conversation_detail(request, conversation_id):
    """Load messages or update a conversation and its deletion state."""
    conversation = Conversation.objects.get(pk=conversation_id, project__archived_at__isnull=True)
    if request.method == 'GET':
        if conversation.deleted_at:
            raise APIError('This conversation was deleted.', 404, 'NOT_FOUND')
        from .chat_service import expire_requests, pending_json
        expire_requests(conversation)
        data = conversation_json(conversation)
        # Load a bounded recent window; older messages can be fetched explicitly.
        try:
            before = int(request.GET['before']) if 'before' in request.GET else None
        except ValueError:
            raise APIError('Use a valid message cursor.') from None
        query = conversation.messages.order_by('-pk')
        if before is not None:
            query = query.filter(pk__lt=before)
        rows = list(query[:201]); selected = rows[:200]
        data.update(messages=[message_json(item) for item in reversed(selected)],
                    olderCursor=selected[-1].pk if len(rows) > 200 else None,
                    pendingRequest=pending_json(conversation))
        return data
    if request.method == 'DELETE':
        conversation.deleted_at = timezone.now()
    else:
        data = payload(request, {'title', 'deleted'})
        conversation.title = text(data, 'title', conversation.title, 200, True)
        if 'deleted' in data:
            conversation.deleted_at = timezone.now() if boolean(data, 'deleted') else None
    conversation.save()
    return conversation_json(conversation)


CONNECTION_FIELDS = {'name', 'url', 'key', 'clearKey'}


def save_connection(connection, data):
    """Validate and save an endpoint while preserving unchanged credentials."""
    connection.name = text(data, 'name', connection.name, 100, True)
    connection.url = text(data, 'url', connection.url, 500, True).rstrip('/')
    URLValidator(schemes=['http', 'https'])(connection.url)
    parsed = urlsplit(connection.url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise APIError('Use a base endpoint URL without credentials, query parameters, or fragments.')
    if 'key' in data:
        key = data['key']
        if not isinstance(key, str) or len(key) > 4096 or '\n' in key or '\r' in key:
            raise APIError('Enter a valid API key.')
        if key:
            connection.api_key = key
    if boolean(data, 'clearKey'):
        connection.api_key = ''
    connection.full_clean(); connection.save()
    return connection_json(connection)


@endpoint('GET', 'POST')
def connections(request):
    """List saved endpoint configurations or create a new one."""
    if request.method == 'GET':
        return [connection_json(item) for item in APIConnection.objects.order_by('created_at')]
    return save_connection(APIConnection(), payload(request, CONNECTION_FIELDS)), 201


@endpoint('PATCH', 'DELETE')
def connection_detail(request, connection_id):
    """Edit or remove an endpoint under the workspace lock."""
    with transaction.atomic():
        workspace_lock()
        connection = APIConnection.objects.select_for_update().get(pk=connection_id)
        if request.method == 'DELETE':
            connection.delete(); return {'id': str(connection_id), 'deleted': True}
        return save_connection(connection, payload(request, CONNECTION_FIELDS))


AGENT_FIELDS = {'name', 'modelName', 'apiId', 'role', 'machine', 'contextWindow'}


def save_agent(agent, data, master=False):
    """Validate and save a model assignment and its configuration."""
    agent.name = text(data, 'name', agent.name or ('Master' if master else ''), 100, True)
    model_name = text(data, 'modelName', agent.ai_model.ollama_name if agent.ai_model_id else '', 100, True)
    api_id = data.get('apiId', str(agent.api_connection_id) if agent.api_connection_id else '')
    if api_id is not None and not isinstance(api_id, str):
        raise APIError('Choose a valid API connection.')
    connection = APIConnection.objects.get(pk=uuid_value(api_id)) if api_id else None
    if not master and not connection:
        raise APIError('Choose an API connection for this specialist.')
    role = 'MASTER' if master else text(data, 'role', agent.role or 'GENERAL', 20)
    if not master and role not in SPECIALIST_ROLES:
        raise APIError('Choose a supported specialist role.')
    context = data.get('contextWindow', agent.context_window)
    if context is not None and (type(context) is not int or not 1 <= context <= 1048576):
        raise APIError('contextWindow must be a positive integer or null.')
    agent.machine = text(data, 'machine', agent.machine, 100)
    agent.api_connection, agent.role, agent.context_window = connection, role, context
    agent.ai_model, _ = AIModel.objects.get_or_create(ollama_name=model_name)
    if agent._state.adding:
        agent.status = 'OFFLINE'
    agent.full_clean(); agent.save()
    return agent_json(agent)


@endpoint('GET', 'POST')
def agents(request):
    """List specialists or create one within the workspace capacity limit."""
    if request.method == 'GET':
        Agent.mark_stale()
        return [agent_json(item) for item in Agent.objects.exclude(role='MASTER').select_related('ai_model').order_by('pk')]
    data = payload(request, AGENT_FIELDS)
    with transaction.atomic():
        workspace_lock()
        if Agent.objects.exclude(role='MASTER').count() >= MAX_SPECIALISTS:
            raise APIError('The workspace supports five specialists.', 409, 'AGENT_LIMIT')
        return save_agent(Agent(), data), 201


@endpoint('PATCH', 'DELETE')
def agent_detail(request, agent_id):
    """Edit or remove a specialist while retaining protected history."""
    with transaction.atomic():
        workspace_lock()
        agent = Agent.objects.exclude(role='MASTER').select_for_update().get(pk=agent_id)
        if request.method == 'DELETE':
            agent.delete(); return {'id': str(agent_id), 'deleted': True}
        return save_agent(agent, payload(request, AGENT_FIELDS))


@endpoint('GET', 'PUT', 'DELETE')
def master(request):
    """Read, save, or remove the single master configuration."""
    if request.method == 'GET':
        return agent_json(master_record())
    with transaction.atomic():
        workspace_lock()
        agent = master_record()
        if request.method == 'DELETE':
            if agent:
                agent.delete()
            return None
        return save_agent(agent or Agent(role='MASTER'), payload(request, AGENT_FIELDS), master=True)


def import_list(data, key, limit):
    """Validate the shape and size of a legacy record collection."""
    value = data.get(key, [])
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(item, dict) for item in value):
        raise APIError(f'Invalid legacy {key}; maximum {limit} records per import.')
    return value


@endpoint('POST')
def import_workspace(request):
    """Import browser data atomically and reuse completed import results."""
    data = payload(request, {'id', 'workspace', 'projectId'}, max_bytes=2000000)
    identifier = uuid_value(data.get('id'))
    legacy = data.get('workspace')
    if not isinstance(legacy, dict):
        raise APIError('Send a valid browser workspace.')
    fingerprint = hashlib.sha256(json.dumps({'workspace': legacy, 'projectId': data.get('projectId')}, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        workspace_lock()
        previous = WorkspaceImport.objects.filter(pk=identifier).first()
        if previous:
            if previous.summary.get('fingerprint') != fingerprint:
                raise APIError('This import identifier was already used for different browser data.', 409, 'IMPORT_CONFLICT')
            return previous.summary['result']
        legacy_apis = import_list(legacy, 'apis', 100)
        legacy_models = import_list(legacy, 'models', 5)
        legacy_chats = import_list(legacy, 'chats', 200)
        if Agent.objects.exclude(role='MASTER').count() + len(legacy_models) > MAX_SPECIALISTS:
            raise APIError('Import would exceed five specialists. Remove unused specialists before importing.', 409, 'AGENT_LIMIT')
        api_map = {}
        for item in legacy_apis:
            connection = APIConnection()
            save_connection(connection, {key: item.get(key, '') for key in ['name', 'url', 'key']})
            api_map[str(item.get('id', ''))] = str(connection.pk)
        for item in legacy_models:
            save_agent(Agent(), {'name': item.get('name'), 'modelName': item.get('modelName'),
                                'apiId': api_map.get(str(item.get('apiId', '')), ''), 'role': item.get('role', 'GENERAL')})
        legacy_master = legacy.get('master') or (legacy_models[0] if legacy_models else None)
        if legacy_master and not master_record():
            if not isinstance(legacy_master, dict):
                raise APIError('Invalid legacy master model.')
            save_agent(Agent(role='MASTER'), {'name': legacy_master.get('name') or 'Master',
                       'modelName': legacy_master.get('modelName'), 'apiId': api_map.get(str(legacy_master.get('apiId', '')), '')}, master=True)
        project = project_record(data['projectId']) if data.get('projectId') else None
        if legacy_chats and project is None:
            project = Project.objects.create(name='Imported browser workspace')
        for item in legacy_chats:
            conversation = Conversation.objects.create(project=project, title=text(item, 'title', 'Imported chat', 200, True))
            messages = import_list(item, 'messages', 1000)
            for message in messages:
                if message.get('role') not in {'user', 'agent'}:
                    raise APIError('Invalid legacy message role.')
                ChatMessage.objects.create(project=project, conversation=conversation,
                    sender='user' if message['role'] == 'user' else 'master', body=text(message, 'text', limit=100000),
                    model_name=text(message, 'category', limit=100), agent_name=text(message, 'agentName', 'Master', 100),
                    is_error=boolean(message, 'error'))
        summary = {'connections': len(legacy_apis), 'specialists': len(legacy_models), 'conversations': len(legacy_chats), 'projectId': str(project.pk) if project else None}
        WorkspaceImport.objects.create(pk=identifier, summary={'fingerprint': fingerprint, 'result': summary})
        return summary, 201
