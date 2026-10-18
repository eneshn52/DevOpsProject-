import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import APIConnection, Agent, ChatMessage, ChatRequest, Conversation, Project, Workspace, WorkspaceImport


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True, OLLAMA_LOOPBACK_HOST='')
class WorkspaceAPITests(TestCase):
    def setUp(self):
        """Create an isolated project and endpoint for each API test."""
        Workspace.objects.get_or_create(pk=1)
        self.project = Project.objects.create(name='Bookstore')
        self.api = APIConnection.objects.create(name='Worker', url='http://127.0.0.1:11434', api_key='private-test-key')

    def send(self, path, data, method='post', client=None):
        """Send a JSON request through Django's middleware and URL routing."""
        return getattr(client or self.client, method)(path, json.dumps(data), content_type='application/json')

    def configure_master(self):
        """Persist a master model that uses the test endpoint."""
        response = self.send('/api/master/', {'name': 'Master', 'modelName': 'test-coder:3b', 'apiId': str(self.api.pk)}, 'put')
        self.assertEqual(response.status_code, 200, response.content)

    def conversation(self):
        """Create a conversation belonging to the test project."""
        return Conversation.objects.create(project=self.project)

    def chat_payload(self, conversation, prompt='Build a bookstore', request_id=None):
        """Build a chat request with an explicit conversation and idempotency key."""
        return {'conversationId': str(conversation.pk), 'requestId': str(request_id or uuid.uuid4()), 'prompt': prompt}

    @staticmethod
    def model_reply(*args, **kwargs):
        """Return a deterministic Ollama response without running a model."""
        return io.BytesIO(json.dumps({'message': {'content': 'A tested response'}}).encode())

    def test_workspace_is_shared_between_clients(self):
        """Ensure configuration comes from the database in a separate browser session."""
        self.configure_master()
        data = Client().get('/api/workspace/').json()['data']
        self.assertEqual(data['master']['modelName'], 'test-coder:3b')
        self.assertEqual(data['projects'][0]['id'], str(self.project.pk))
        self.assertNotIn('private-test-key', json.dumps(data))
        self.assertTrue(data['apis'][0]['hasKey'])

    def test_no_automatic_master_is_created(self):
        """Keep an unconfigured workspace visibly unconfigured."""
        self.assertIsNone(self.client.get('/api/workspace/').json()['data']['master'])
        self.assertFalse(Agent.objects.filter(role='MASTER').exists())

    def test_project_create_patch_archive(self):
        """Persist project edits and archive projects without deleting their records."""
        response = self.send('/api/projects/', {'name': 'Store', 'requirements': 'User authentication'})
        self.assertEqual(response.status_code, 201)
        identifier = response.json()['data']['id']
        response = self.send(f'/api/projects/{identifier}/', {'name': 'Updated store'}, 'patch')
        self.assertEqual(response.json()['data']['name'], 'Updated store')
        self.client.delete(f'/api/projects/{identifier}/')
        self.assertTrue(Project.objects.filter(pk=identifier).exists())
        self.assertNotIn(identifier, [item['id'] for item in self.client.get('/api/projects/').json()['data']])

    def test_invalid_payloads_are_rejected(self):
        """Reject malformed JSON, unexpected fields, and incorrectly typed values."""
        for data in [[], {'name': 10}, {'name': 'Store', 'unexpected': True}, {'name': '   '}]:
            with self.subTest(data=data):
                self.assertEqual(self.send('/api/projects/', data).status_code, 400)
        self.assertEqual(self.client.post('/api/projects/', '{', content_type='application/json').status_code, 400)
        self.assertEqual(self.send('/api/conversations/', {'projectId': True}).status_code, 400)

    def test_csrf_is_required(self):
        """Require the CSRF token for state-changing browser requests."""
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(self.send('/api/projects/', {'name': 'CSRF'}, client=client).status_code, 403)
        client.get('/')
        token = client.cookies['csrftoken'].value
        response = client.post('/api/projects/', json.dumps({'name': 'CSRF'}), content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)

    def test_remote_anonymous_access_is_denied(self):
        """Prevent anonymous remote clients from reading the shared workspace."""
        self.assertEqual(self.client.get('/api/workspace/', REMOTE_ADDR='192.0.2.10').status_code, 403)

    @override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=False)
    def test_staff_session_allows_remote_access(self):
        """Allow a signed-in workspace administrator when local bypass is disabled."""
        self.assertEqual(self.client.get('/api/workspace/').status_code, 403)
        user = get_user_model().objects.create_user(username='operator', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get('/api/workspace/', REMOTE_ADDR='192.0.2.10').status_code, 200)

    def test_credentials_are_write_only_and_can_be_cleared(self):
        """Preserve blank credential edits and clear keys only on an explicit request."""
        path = f'/api/connections/{self.api.pk}/'
        response = self.send(path, {'name': 'Renamed', 'key': ''}, 'patch')
        self.assertEqual(response.status_code, 200)
        self.api.refresh_from_db()
        self.assertEqual(self.api.api_key, 'private-test-key')
        self.assertNotIn('private-test-key', response.content.decode())
        self.assertNotIn('api_key', response.json()['data'])
        self.assertEqual(self.send(path, {'clearKey': True}, 'patch').status_code, 200)
        self.api.refresh_from_db()
        self.assertEqual(self.api.api_key, '')

    def test_connection_validation(self):
        """Reject unsupported schemes, embedded credentials, and malformed headers."""
        for url in ['file:///etc/passwd', 'ftp://example.com', 'http://user:password@localhost:11434', 'http://localhost:11434?token=secret']:
            self.assertEqual(self.send('/api/connections/', {'name': 'Invalid', 'url': url}).status_code, 400)
        self.assertEqual(self.send('/api/connections/', {'name': 'Invalid', 'url': 'http://localhost:11434', 'key': 'bad\nheader'}).status_code, 400)

    def test_specialist_limit_and_roles(self):
        """Enforce the specialist limit on the server and reject unsupported roles."""
        data = {'name': 'Coder', 'modelName': 'test:3b', 'apiId': str(self.api.pk), 'role': 'BACKEND'}
        for _ in range(5):
            self.assertEqual(self.send('/api/agents/', data).status_code, 201)
        self.assertEqual(self.send('/api/agents/', data).status_code, 409)
        agent = Agent.objects.exclude(role='MASTER').first()
        self.assertEqual(self.send(f'/api/agents/{agent.pk}/', {'role': 'MASTER'}, 'patch').status_code, 400)
        self.assertEqual(self.send(f'/api/agents/{agent.pk}/', {'contextWindow': True}, 'patch').status_code, 400)

    def test_connection_deletion_unlinks_agents(self):
        """Remove an endpoint without removing its configured model or history."""
        self.configure_master()
        self.assertEqual(self.client.delete(f'/api/connections/{self.api.pk}/').status_code, 200)
        self.assertIsNone(Agent.objects.get(role='MASTER').api_connection_id)

    def test_conversation_search_delete_restore(self):
        """Search persisted messages and restore a soft-deleted conversation."""
        conversation = self.conversation()
        ChatMessage.objects.create(project=self.project, conversation=conversation, sender='user', body='Unique database search')
        response = self.client.get('/api/conversations/', {'projectId': self.project.pk, 'search': 'Unique database'})
        self.assertEqual(len(response.json()['data']['items']), 1)
        self.client.delete(f'/api/conversations/{conversation.pk}/')
        self.assertEqual(self.client.get(f'/api/conversations/{conversation.pk}/').status_code, 404)
        self.send(f'/api/conversations/{conversation.pk}/', {'deleted': False}, 'patch')
        self.assertEqual(len(self.client.get(f'/api/conversations/{conversation.pk}/').json()['data']['messages']), 1)

    def test_message_pagination(self):
        """Retrieve older messages without dropping or repeating message IDs."""
        conversation = self.conversation()
        ChatMessage.objects.bulk_create([ChatMessage(project=self.project, conversation=conversation, body=str(index)) for index in range(205)])
        first = self.client.get(f'/api/conversations/{conversation.pk}/').json()['data']
        second = self.client.get(f'/api/conversations/{conversation.pk}/', {'before': first['olderCursor']}).json()['data']
        self.assertEqual(len(first['messages']), 200)
        self.assertEqual(len(second['messages']), 5)
        self.assertFalse({item['id'] for item in first['messages']} & {item['id'] for item in second['messages']})

    def test_chat_requires_a_configured_master(self):
        """Reject chat requests before writing messages when no master is configured."""
        response = self.send('/api/chat/', self.chat_payload(self.conversation()))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(ChatMessage.objects.count(), 0)

    @patch('django_modules.chat_service.urllib.request.urlopen')
    def test_chat_uses_server_configuration_and_persists_history(self, model):
        """Use saved credentials and include only the selected conversation's history."""
        model.side_effect = self.model_reply
        self.configure_master()
        conversation = self.conversation()
        other = self.conversation()
        ChatMessage.objects.create(project=self.project, conversation=other, sender='user', body='Other conversation secret')
        response = self.send('/api/chat/', self.chat_payload(conversation))
        self.assertEqual(response.status_code, 200, response.content)
        request = model.call_args.args[0]
        self.assertEqual(request.full_url, f'{self.api.url}/api/chat')
        self.assertEqual(request.headers['Authorization'], 'Bearer private-test-key')
        response = self.send('/api/chat/', self.chat_payload(conversation, 'Add tests'))
        sent = json.loads(model.call_args.args[0].data)
        self.assertEqual([item['role'] for item in sent['messages']], ['user', 'assistant', 'user'])
        self.assertNotIn('Other conversation secret', json.dumps(sent))
        data = Client().get(f'/api/conversations/{conversation.pk}/').json()['data']
        self.assertEqual(len(data['messages']), 4)
        self.assertNotIn('private-test-key', response.content.decode())

    @patch('django_modules.chat_service.urllib.request.urlopen')
    def test_chat_request_is_idempotent(self, model):
        """Replay an existing result without generating or persisting it twice."""
        model.side_effect = self.model_reply
        self.configure_master()
        conversation = self.conversation()
        data = self.chat_payload(conversation)
        first = self.send('/api/chat/', data)
        second = self.send('/api/chat/', data)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(model.call_count, 1)
        self.assertEqual(conversation.messages.count(), 2)
        data['prompt'] = 'A different prompt'
        self.assertEqual(self.send('/api/chat/', data).status_code, 409)

    @patch('django_modules.chat_service.urllib.request.urlopen', side_effect=URLError('private-test-key'))
    def test_failed_chat_is_persisted_without_exposing_credentials(self, model):
        """Persist a failed exchange and avoid leaking raw transport errors."""
        self.configure_master()
        conversation = self.conversation()
        data = self.chat_payload(conversation)
        response = self.send('/api/chat/', data)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('private-test-key', response.content.decode())
        self.assertEqual(ChatRequest.objects.get().status, 'FAILED')
        self.assertTrue(conversation.messages.get(sender='master').is_error)
        self.assertEqual(self.send('/api/chat/', data).status_code, 502)
        self.assertEqual(model.call_count, 1)

    @patch('django_modules.chat_service.urllib.request.urlopen')
    def test_invalid_model_response_is_recorded(self, model):
        """Turn malformed model output into a durable, visible failed response."""
        model.return_value = io.BytesIO(b'[]')
        self.configure_master()
        response = self.send('/api/chat/', self.chat_payload(self.conversation()))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(ChatRequest.objects.get().status, 'FAILED')

    def test_stale_chat_is_recovered(self):
        """Expire a request left processing after a server interruption."""
        self.configure_master()
        conversation = self.conversation()
        message = ChatMessage.objects.create(project=self.project, conversation=conversation, sender='user', body='Interrupted')
        chat_request = ChatRequest.objects.create(conversation=conversation, user_message=message, model_name='test:3b', agent_name='Master')
        self.assertEqual(self.send('/api/chat/', self.chat_payload(conversation)).status_code, 409)
        ChatRequest.objects.filter(pk=chat_request.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        self.client.get(f'/api/conversations/{conversation.pk}/')
        chat_request.refresh_from_db()
        self.assertEqual(chat_request.status, 'FAILED')
        self.assertTrue(chat_request.reply.is_error)

    def test_chat_cannot_override_server_endpoint(self):
        """Reject model URLs and credentials supplied through the chat endpoint."""
        data = self.chat_payload(self.conversation())
        data['api_url'] = 'http://unregistered.invalid'
        self.assertEqual(self.send('/api/chat/', data).status_code, 400)

    def legacy_data(self):
        """Build an old browser workspace containing an endpoint, model, and chat."""
        return {'apis': [{'id': 'old-api', 'name': 'Imported worker', 'url': 'http://localhost:11434', 'key': 'import-secret'}],
                'models': [{'id': 'old-model', 'name': 'Imported coder', 'modelName': 'imported:3b', 'apiId': 'old-api'}],
                'chats': [{'title': 'Imported chat', 'messages': [{'role': 'user', 'text': 'Keep my old message'}]}]}

    def test_browser_import_is_atomic_and_idempotent(self):
        """Import legacy data once and leave existing master configuration intact."""
        self.configure_master()
        data = {'id': str(uuid.uuid4()), 'workspace': self.legacy_data(), 'projectId': str(self.project.pk)}
        first = self.send('/api/workspace/import/', data)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(self.send('/api/workspace/import/', data).status_code, 200)
        self.assertEqual(WorkspaceImport.objects.count(), 1)
        self.assertEqual(ChatMessage.objects.filter(body='Keep my old message').count(), 1)
        self.assertEqual(Agent.objects.get(role='MASTER').ai_model.ollama_name, 'test-coder:3b')
        self.assertNotIn('import-secret', Client().get('/api/workspace/').content.decode())
        data['workspace']['chats'][0]['messages'][0]['text'] = 'Changed since the import'
        self.assertEqual(self.send('/api/workspace/import/', data).status_code, 409)
        self.assertFalse(ChatMessage.objects.filter(body='Changed since the import').exists())

    def test_failed_import_rolls_back_connections(self):
        """Retain an unchanged database when any imported record is invalid."""
        legacy = self.legacy_data(); legacy['models'][0]['modelName'] = ''
        count = APIConnection.objects.count()
        response = self.send('/api/workspace/import/', {'id': str(uuid.uuid4()), 'workspace': legacy})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(APIConnection.objects.count(), count)
        self.assertEqual(WorkspaceImport.objects.count(), 0)


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL row locks are required')
@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class WorkspaceConcurrencyTests(TransactionTestCase):
    def test_concurrent_agent_creates_obey_limit(self):
        """Serialize competing creates so two clients cannot exceed five specialists."""
        Workspace.objects.get_or_create(pk=1)
        endpoint = APIConnection.objects.create(name='Worker', url='http://localhost:11434')
        for index in range(4):
            Agent.objects.create(name=f'Agent {index}')
        barrier = Barrier(2)

        def create_agent():
            """Submit a competing request using an independent database connection."""
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = Client().post('/api/agents/', json.dumps({'name': 'Concurrent coder', 'modelName': 'test:3b', 'apiId': str(endpoint.pk)}), content_type='application/json')
                return response.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create_agent) for _ in range(2)]
            statuses = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(Agent.objects.count(), 5)


class WorkspaceMigrationTests(TransactionTestCase):
    def test_existing_messages_survive_forward_migration(self):
        """Backfill project and orphan messages into conversations without data loss."""
        before = [('django_modules', '0003_update_models_to_erd')]
        executor = MigrationExecutor(connection)
        after = executor.loader.graph.leaf_nodes('django_modules')
        try:
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            project = old.get_model('django_modules', 'Project').objects.create(name='Existing project')
            old.get_model('django_modules', 'ChatMessage').objects.create(project_id=project.pk, body='Existing message')
            old.get_model('django_modules', 'ChatMessage').objects.create(body='Orphan message')
            MigrationExecutor(connection).migrate(after)
            self.assertIsNotNone(ChatMessage.objects.get(body='Existing message').conversation_id)
            self.assertIsNotNone(ChatMessage.objects.get(body='Orphan message').conversation_id)
            self.assertEqual(ChatMessage.objects.count(), 2)
            self.assertTrue(Workspace.objects.filter(pk=1).exists())
        finally:
            MigrationExecutor(connection).migrate(after)
