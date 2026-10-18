"""Persisted chat relay; model execution happens outside database transactions."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ChatMessage, ChatRequest, Conversation
from .workspace_api import APIError, endpoint, master_record, message_json, payload, text, uuid_value

MAX_PROMPT_CHARS = 32000
MAX_RESPONSE_BYTES = 2000000
MAX_HISTORY_CHARS = 64000


def finish_request(chat_request, answer, failed=False):
    """Persist one model reply and finalize its request atomically."""
    with transaction.atomic():
        Conversation.objects.select_for_update().get(pk=chat_request.conversation_id)
        current = ChatRequest.objects.select_for_update().get(pk=chat_request.pk)
        if current.status != 'PROCESSING':
            return current
        current.reply = ChatMessage.objects.create(
            project_id=current.conversation.project_id, conversation_id=current.conversation_id,
            sender='master', body=answer, model_name=current.model_name,
            agent_name=current.agent_name, is_error=failed,
        )
        current.status = 'FAILED' if failed else 'COMPLETED'
        current.save(update_fields=['reply', 'status', 'updated_at'])
        Conversation.objects.filter(pk=current.conversation_id).update(updated_at=timezone.now())
        return current


def expire_locked(conversation):
    """Mark overdue requests as failed while holding the conversation lock."""
    cutoff = timezone.now() - timedelta(seconds=settings.OLLAMA_REQUEST_TIMEOUT + 60)
    for chat_request in conversation.requests.filter(status='PROCESSING', created_at__lt=cutoff):
        finish_request(chat_request, 'The previous response was interrupted or timed out. You can send a new message.', failed=True)


def expire_requests(conversation):
    """Recover interrupted chat requests without racing active updates."""
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
        expire_locked(conversation)


def pending_json(conversation):
    """Describe the response currently being generated for a conversation."""
    current = conversation.requests.filter(status='PROCESSING').first()
    return {'id': str(current.pk), 'agentName': current.agent_name, 'modelName': current.model_name} if current else None


def request_json(chat_request):
    """Serialize a saved exchange for initial delivery or idempotent replay."""
    return {'requestId': str(chat_request.pk), 'conversationId': str(chat_request.conversation_id),
            'status': chat_request.status, 'userMessage': message_json(chat_request.user_message),
            'reply': message_json(chat_request.reply) if chat_request.reply_id else None,
            'answer': chat_request.reply.body if chat_request.reply_id and chat_request.status == 'COMPLETED' else '',
            'category': chat_request.model_name,
            'error': chat_request.reply.body if chat_request.reply_id and chat_request.status == 'FAILED' else None}


def begin_request(conversation_id, request_id, prompt):
    """Reserve a chat exchange and snapshot the configured model endpoint."""
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().get(
            pk=conversation_id, deleted_at__isnull=True, project__archived_at__isnull=True,
        )
        expire_locked(conversation)
        previous = ChatRequest.objects.select_related('user_message', 'reply').filter(pk=request_id).first()
        if previous:
            if previous.conversation_id != conversation.pk or previous.user_message.body != prompt:
                raise APIError('This request identifier was already used for a different message.', 409, 'REQUEST_CONFLICT')
            if previous.status == 'PROCESSING':
                raise APIError('This response is still being generated.', 409, 'REQUEST_IN_PROGRESS')
            return previous, None
        if conversation.requests.filter(status='PROCESSING').exists():
            raise APIError('Wait for the current response before sending another message.', 409, 'CONVERSATION_BUSY')
        master = master_record()
        if not master or not master.ai_model:
            raise APIError('Configure a master model in Settings before sending a message.', 409, 'MASTER_NOT_CONFIGURED')
        message = ChatMessage.objects.create(project=conversation.project, conversation=conversation, sender='user', body=prompt)
        chat_request = ChatRequest.objects.create(id=request_id, conversation=conversation, user_message=message,
                                                 model_name=master.ai_model.ollama_name, agent_name=master.name)
        if conversation.title == 'New chat':
            conversation.title = prompt[:80]
        conversation.save(update_fields=['title', 'updated_at'])
        connection = master.api_connection
        endpoint_config = {'url': connection.url if connection else settings.OLLAMA_DEFAULT_URL,
                           'key': connection.api_key if connection else ''}
        return chat_request, endpoint_config


def history_messages(chat_request):
    """Build bounded model context from this conversation only."""
    query = ChatMessage.objects.filter(conversation_id=chat_request.conversation_id, is_error=False)
    query = query.exclude(request_as_user__status='FAILED').order_by('-pk')
    messages, size = [], 0
    for message in query[:40]:
        if size + len(message.body) > MAX_HISTORY_CHARS:
            break
        messages.append({'role': 'user' if message.sender == 'user' else 'assistant', 'content': message.body})
        size += len(message.body)
    return list(reversed(messages))


def model_endpoint_url(url):
    """Translate saved host-loopback endpoints when Django runs inside Docker."""
    parsed = urlsplit(url)
    if settings.OLLAMA_LOOPBACK_HOST and parsed.hostname in {'localhost', '127.0.0.1', '::1'}:
        hostname = settings.OLLAMA_LOOPBACK_HOST
        if ':' in hostname:
            hostname = f'[{hostname}]'
        address = f'{hostname}:{parsed.port}' if parsed.port else hostname
        return urlunsplit((parsed.scheme, address, parsed.path, parsed.query, parsed.fragment))
    return url


def call_model(model_name, endpoint_config, messages, response_format=None):
    """Call a saved Ollama endpoint with bounded output and optional structured JSON."""
    content = {'model': model_name, 'messages': messages, 'stream': False}
    if response_format is not None:
        content.update(format=response_format, options={'temperature': 0})
    body = json.dumps(content).encode()
    headers = {'Content-Type': 'application/json'}
    if endpoint_config['key']:
        headers['Authorization'] = f"Bearer {endpoint_config['key']}"
    url = model_endpoint_url(endpoint_config['url']).rstrip('/')
    request = urllib.request.Request(f'{url}/api/chat', data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=settings.OLLAMA_REQUEST_TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise APIError('The model response exceeded the size limit.', 502, 'MODEL_RESPONSE_TOO_LARGE')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Expected an object')
        message = result.get('message', {})
        answer = message.get('content') if isinstance(message, dict) else None
        answer = answer or result.get('response')
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('Empty model response')
        return answer
    except urllib.error.HTTPError as error:
        raise APIError(f'The model endpoint returned HTTP {error.code}. Check the model and endpoint settings.', 502, 'MODEL_HTTP_ERROR') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise APIError('Could not reach the model endpoint or the response timed out. Check that Ollama is running.', 502, 'MODEL_UNAVAILABLE') from None
    except (ValueError, UnicodeDecodeError):
        raise APIError('The model endpoint returned an empty or invalid response.', 502, 'MODEL_INVALID_RESPONSE') from None


def generate_reply(chat_request, endpoint_config):
    """Generate a chat reply using only its saved conversation context."""
    return call_model(chat_request.model_name, endpoint_config, history_messages(chat_request))


@endpoint('POST')
def chat_api(request):
    """Generate or replay a persisted chat response for an explicit conversation."""
    data = payload(request, {'prompt', 'conversationId', 'requestId'})
    prompt = text(data, 'prompt', limit=MAX_PROMPT_CHARS, required=True)
    conversation_id, request_id = uuid_value(data.get('conversationId')), uuid_value(data.get('requestId'))
    chat_request, endpoint_config = begin_request(conversation_id, request_id, prompt)
    if endpoint_config is None:
        return request_json(chat_request), 502 if chat_request.status == 'FAILED' else 200
    try:
        answer = generate_reply(chat_request, endpoint_config)
    except APIError as error:
        finished = finish_request(chat_request, str(error), failed=True)
        return request_json(finished), error.status
    finished = finish_request(chat_request, answer)
    return request_json(finished), 502 if finished.status == 'FAILED' else 200
