"""Exercise saved planning through real Django routes and PostgreSQL transactions."""
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest import skipUnless
from unittest.mock import patch
from urllib.error import URLError

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .admin import TaskDependencyForm
from .models import AIModel, APIConnection, Agent, Project, Task, TaskDependency, TaskPlan
from .task_planning import PLAN_SCHEMA
from .task_distribution import ready_tasks
from .task_weights import TASK_WEIGHTS


def example_plan():
    """Build an intentionally unordered plan containing parallel dependent work."""
    tasks = []
    for key, kind, dependencies in [('test', 'TESTING', ['backend', 'frontend']),
                                     ('contract', 'GENERAL', []),
                                     ('frontend', 'FRONTEND', ['contract']),
                                     ('backend', 'BACKEND', ['contract'])]:
        tasks.append({'key': key, 'title': f'Implement {key}', 'type': kind,
                      'instructions': f'Complete the {key} work using prerequisite outputs.',
                      'requiredCapabilities': ['Django' if kind == 'BACKEND' else 'Software development'],
                      'expectedOutput': f'Reviewable {key} artifact',
                      'acceptanceCriteria': ['The stated contract is satisfied.'], 'dependsOn': dependencies})
    return {'title': 'Login implementation', 'summary': 'Assumes a Django application.', 'tasks': tasks}


def ollama_plan(value):
    """Wrap a structured plan as an Ollama chat response."""
    return io.BytesIO(json.dumps({'message': {'content': json.dumps(value)}}).encode())


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True, OLLAMA_LOOPBACK_HOST='')
class TaskPlanningTests(TestCase):
    def setUp(self):
        """Create a project and explicitly configured local planner for each test."""
        self.project = Project.objects.create(name='Login app', requirements_text='Use PostgreSQL.', spec_summary='Existing Django project.')
        self.connection = APIConnection.objects.create(name='Planner endpoint', url='http://localhost:11434', api_key='planner-secret')
        self.master = Agent.objects.create(name='Master', role='MASTER', api_connection=self.connection,
                                           ai_model=AIModel.objects.create(ollama_name='planner-test:3b'))
        self.url = f'/api/projects/{self.project.pk}/plans/'
        self.body = {'id': str(uuid.uuid4()), 'request': 'Build a login system.'}

    def post(self, body=None, client=None):
        """Submit a planning request through middleware and URL dispatch."""
        return (client or self.client).post(self.url, json.dumps(body or self.body), content_type='application/json')

    def generate(self, value=None, body=None):
        """Generate a plan with deterministic model output and no live model dependency."""
        with patch('django_modules.chat_service.urllib.request.urlopen', return_value=ollama_plan(value or example_plan())):
            return self.post(body)

    def test_generates_persistent_dag_with_structured_requirements(self):
        """Save all task requirements and dependencies, retaining unrelated existing work."""
        legacy = Task.objects.create(project=self.project, title='Existing task')
        response = self.generate()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()['data']
        self.assertEqual(data['status'], 'READY')
        self.assertEqual(data['taskCount'], 4)
        self.assertEqual([task['key'] for task in data['tasks']], ['contract', 'frontend', 'backend', 'test'])
        self.assertTrue(all(task['status'] == 'PENDING' for task in data['tasks']))
        self.assertEqual(TaskDependency.objects.count(), 4)
        self.assertSetEqual(set(TaskDependency.objects.values_list('task__plan_key', 'depends_on__plan_key')),
                            {('frontend', 'contract'), ('backend', 'contract'),
                             ('test', 'backend'), ('test', 'frontend')})
        self.assertEqual({task['key']: task['dependsOn'] for task in data['tasks']},
                         {'contract': [], 'frontend': ['contract'], 'backend': ['contract'],
                          'test': ['backend', 'frontend']})
        self.assertEqual(Task.objects.get(plan_key='test').acceptance_criteria, ['The stated contract is satisfied.'])
        self.assertEqual(Task.objects.get(plan_key='test').expected_output, 'Reviewable test artifact')
        self.assertTrue(Task.objects.filter(pk=legacy.pk, plan__isnull=True).exists())
        saved = Client().get(f"{self.url}{data['id']}/").json()['data']
        self.assertEqual(saved, data)
        self.assertNotIn('planner-secret', json.dumps(saved))
        self.assertNotIn('planner-secret', self.client.get(self.url).content.decode())

    def test_generated_dependencies_release_parallel_branches_and_wait_for_the_join(self):
        """A saved master plan releases independent branches and blocks its join until both finish."""
        self.assertEqual(self.generate().status_code, 201)
        tasks = {task.plan_key: task for task in Task.objects.all()}
        self.assertEqual([task.plan_key for task in ready_tasks()], ['contract'])
        tasks['contract'].status = 'COMPLETED'
        tasks['contract'].save(update_fields=['status'])
        self.assertSetEqual({task.plan_key for task in ready_tasks()}, {'frontend', 'backend'})
        tasks['backend'].status = 'COMPLETED'
        tasks['backend'].save(update_fields=['status'])
        self.assertEqual([task.plan_key for task in ready_tasks()], ['frontend'])
        for status in ['ASSIGNED', 'IN_PROGRESS', 'FAILED']:
            with self.subTest(prerequisite_status=status):
                tasks['frontend'].status = status
                tasks['frontend'].save(update_fields=['status'])
                self.assertEqual(ready_tasks(), [])
                tasks['test'].refresh_from_db()
                self.assertEqual((tasks['test'].status, tasks['test'].assigned_agent_id), ('PENDING', None))
        tasks['frontend'].status = 'COMPLETED'
        tasks['frontend'].save(update_fields=['status'])
        self.assertEqual([task.plan_key for task in ready_tasks()], ['test'])
        saved = self.client.get(f'{self.url}{tasks["test"].plan_id}/').json()['data']
        self.assertEqual(next(row for row in saved['tasks'] if row['key'] == 'test')['dependsOn'],
                         ['backend', 'frontend'])

    def test_dependency_write_failure_rolls_back_the_whole_graph(self):
        """A failed edge write leaves existing graphs intact and no partially saved new tasks."""
        self.assertEqual(self.generate().status_code, 201)
        original_task_ids = set(Task.objects.values_list('pk', flat=True))
        original_edge_ids = set(TaskDependency.objects.values_list('pk', flat=True))
        original_write = TaskDependency.objects.bulk_create

        def fail_after_writing_edges(*args, **kwargs):
            """Simulate a database failure after edge insertion inside the plan transaction."""
            original_write(*args, **kwargs)
            raise DatabaseError('Dependency storage unavailable')

        with patch('django_modules.task_planning.TaskDependency.objects.bulk_create', side_effect=fail_after_writing_edges):
            response = self.generate(body={**self.body, 'id': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()['code'], 'DATABASE_UNAVAILABLE')
        self.assertSetEqual(set(Task.objects.values_list('pk', flat=True)), original_task_ids)
        self.assertSetEqual(set(TaskDependency.objects.values_list('pk', flat=True)), original_edge_ids)
        self.assertEqual(TaskPlan.objects.filter(status='READY').count(), 1)

    def test_same_task_keys_in_separate_plans_keep_dependencies_local(self):
        """Task keys resolve within their own plan, including when another plan reuses every key."""
        first = self.generate().json()['data']
        second = self.generate(body={**self.body, 'id': str(uuid.uuid4())}).json()['data']
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(TaskDependency.objects.count(), 8)
        for edge in TaskDependency.objects.select_related('task', 'depends_on'):
            self.assertEqual(edge.task.plan_id, edge.depends_on.plan_id)
            self.assertEqual(edge.task.project_id, edge.depends_on.project_id)

    def test_classifies_all_task_types_and_exposes_persisted_weights(self):
        """The master supplies types; Django assigns weights to every saved task and response."""
        value = example_plan()
        template = value['tasks'][0]
        value['tasks'] = [dict(template, key=kind.lower(), type=kind, dependsOn=[]) for kind in TASK_WEIGHTS]
        response = self.generate(value)
        self.assertEqual(response.status_code, 201, response.content)
        for row in response.json()['data']['tasks']:
            self.assertEqual(row['capabilityWeights'], TASK_WEIGHTS[row['type']])
            self.assertEqual(Task.objects.get(pk=row['id']).capability_weights, row['capabilityWeights'])

    def test_master_cannot_supply_weights_or_unsupported_types(self):
        """Reject invented classifications and injected weights before any tasks are persisted."""
        for extra in [{'type': 'MASTER'}, {'type': 'INVENTED'}, {'capabilityWeights': {'coding': 1}}]:
            with self.subTest(extra=extra):
                value = example_plan()
                value['tasks'][0].update(extra)
                response = self.generate(value, body={'id': str(uuid.uuid4()), 'request': 'Build login.'})
                self.assertEqual(response.status_code, 502, response.content)
                self.assertEqual(response.json()['data']['errorCode'], 'INVALID_PLAN')
                self.assertFalse(Task.objects.exists())

    def test_uses_server_model_credentials_and_project_context(self):
        """Send bounded project context and the saved master configuration to Ollama."""
        other = Project.objects.create(name='Private other project', requirements_text='Unrelated secret context')
        self.assertIsNotNone(other.pk)
        self.project.requirements_text = 'r' * 10000
        self.project.spec_summary = 's' * 6000
        self.project.save()
        with patch('django_modules.chat_service.urllib.request.urlopen', return_value=ollama_plan(example_plan())) as call:
            self.assertEqual(self.post().status_code, 201)
        request = call.call_args.args[0]
        content = json.loads(request.data)
        self.assertEqual(request.full_url, 'http://localhost:11434/api/chat')
        self.assertEqual(request.get_header('Authorization'), 'Bearer planner-secret')
        self.assertEqual(content['model'], 'planner-test:3b')
        self.assertEqual(content['format'], PLAN_SCHEMA)
        context = json.loads(content['messages'][1]['content'])
        self.assertEqual(len(context['project']['requirements']), 6000)
        self.assertEqual(len(context['project']['summary']), 2000)
        self.assertNotIn('Unrelated secret context', json.dumps(content))
        self.assertNotIn('planner-secret', json.dumps(content))
        self.assertFalse(content['stream'])

    def test_request_replay_does_not_generate_duplicate_tasks(self):
        """Replay the same ID and content even if the master is later removed."""
        first = self.generate()
        self.master.delete()
        with patch('django_modules.task_planning.call_model') as call:
            replay = self.post()
        call.assert_not_called()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json()['data'], replay.json()['data'])
        self.assertEqual(Task.objects.count(), 4)
        self.assertEqual(TaskDependency.objects.count(), 4)

    def test_reused_identifier_rejects_changed_request_or_project(self):
        """Prevent one idempotency identifier from representing different work."""
        self.generate()
        self.assertEqual(self.post({**self.body, 'request': 'Something else'}).status_code, 409)
        other = Project.objects.create(name='Other')
        self.url = f'/api/projects/{other.pk}/plans/'
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(TaskPlan.objects.count(), 1)

    def test_requires_master_and_rejects_client_endpoint_overrides(self):
        """Require explicit server configuration before persisting or generating a plan."""
        for field in ['api_url', 'api_key', 'model', 'plan', 'projectId']:
            with self.subTest(field=field):
                self.assertEqual(self.post({**self.body, field: 'override'}).status_code, 400)
        self.master.delete()
        with patch('django_modules.task_planning.call_model') as call:
            response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'MASTER_NOT_CONFIGURED')
        self.assertFalse(TaskPlan.objects.exists())
        call.assert_not_called()

    def test_rejects_invalid_request_shapes_before_model_call(self):
        """Reject malformed UUIDs, empty requests, incorrect types, and oversized inputs."""
        for body in [{'id': 'bad', 'request': 'Plan'}, {'id': self.body['id'], 'request': ''},
                     {'id': self.body['id'], 'request': 42}, {'id': self.body['id'], 'request': 'x' * 8001}]:
            with self.subTest(body_type=type(body['request'])):
                self.assertEqual(self.post(body).status_code, 400)
        self.assertFalse(TaskPlan.objects.exists())

    def test_invalid_graphs_never_persist_partial_tasks(self):
        """Reject cycles, self-links, missing/external references, and duplicated task keys."""
        for kind in ['cycle', 'self', 'missing', 'external_id', 'duplicate_key', 'duplicate_dependency']:
            with self.subTest(kind=kind):
                value = example_plan()
                if kind == 'cycle':
                    value['tasks'][1]['dependsOn'] = ['test']
                elif kind == 'self':
                    value['tasks'][1]['dependsOn'] = ['contract']
                elif kind == 'missing':
                    value['tasks'][1]['dependsOn'] = ['nonexistent']
                elif kind == 'external_id':
                    external = Task.objects.create(project=Project.objects.create(name='Other'), title='External')
                    value['tasks'][1]['dependsOn'] = [str(external.pk)]
                elif kind == 'duplicate_key':
                    value['tasks'][0]['key'] = 'contract'
                else:
                    value['tasks'][0]['dependsOn'] = ['backend', 'backend']
                response = self.generate(value, {**self.body, 'id': str(uuid.uuid4())})
                self.assertEqual(response.status_code, 502, response.content)
                self.assertEqual(response.json()['data']['errorCode'], 'INVALID_PLAN')
                self.assertEqual(response.json()['data']['tasks'], [])
                self.assertFalse(Task.objects.filter(plan__isnull=False).exists())
                self.assertFalse(TaskDependency.objects.exists())

    def test_rejects_incomplete_unbounded_or_mistyped_task_details(self):
        """Validate structured requirements independently of Ollama's schema handling."""
        invalid = []
        for field, value in [('requiredCapabilities', []), ('acceptanceCriteria', 'works'),
                             ('acceptanceCriteria', [' ']), ('expectedOutput', ''),
                             ('instructions', 'x' * 4001), ('type', 'MASTER'), ('dependsOn', None)]:
            plan = example_plan(); plan['tasks'][0][field] = value; invalid.append(plan)
        plan = example_plan(); del plan['tasks'][0]['expectedOutput']; invalid.append(plan)
        plan = example_plan(); plan['tasks'][0]['agentId'] = 99; invalid.append(plan)
        plan = example_plan(); plan['tasks'] = []; invalid.append(plan)
        plan = example_plan(); plan['tasks'] = plan['tasks'] * 6; invalid.append(plan)
        invalid.extend([[], {'title': 'Only a title'}])
        for index, value in enumerate(invalid):
            with self.subTest(case=index):
                with patch('django_modules.chat_service.urllib.request.urlopen', return_value=ollama_plan(value)):
                    response = self.post({**self.body, 'id': str(uuid.uuid4())})
                self.assertEqual(response.status_code, 502, response.content)
                self.assertEqual(response.json()['data']['errorCode'], 'INVALID_PLAN')
        self.assertFalse(Task.objects.exists())

    def test_model_failures_are_saved_and_replayed_without_leaking_secrets(self):
        """Preserve safe failure details while permitting a new request ID to retry."""
        with patch('django_modules.chat_service.urllib.request.urlopen', side_effect=URLError('planner-secret')):
            failed = self.post()
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn('planner-secret', failed.content.decode())
        with patch('django_modules.task_planning.call_model') as call:
            replay = self.post()
        call.assert_not_called()
        self.assertEqual(failed.json()['data'], replay.json()['data'])
        self.assertEqual(self.generate(body={**self.body, 'id': str(uuid.uuid4())}).status_code, 201)
        self.assertEqual(TaskPlan.objects.count(), 2)

    def test_rejects_non_json_model_output(self):
        """Record explanatory model prose as a failed generation rather than parsing heuristically."""
        with patch('django_modules.task_planning.call_model', return_value='Here is your plan: ...'):
            response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['data']['errorCode'], 'INVALID_PLAN')
        self.assertFalse(Task.objects.exists())

    def test_stale_attempt_recovery_prevents_late_results(self):
        """Recover interrupted generation and refuse stale output without creating any tasks."""
        def delayed_response(*args, **kwargs):
            """Simulate generation that finishes after its deadline has expired."""
            TaskPlan.objects.update(created_at=timezone.now() - timedelta(days=1))
            return json.dumps(example_plan())

        with patch('django_modules.task_planning.call_model', side_effect=delayed_response):
            response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['data']['errorCode'], 'PLAN_INTERRUPTED')
        self.assertFalse(Task.objects.exists())
        plan = TaskPlan.objects.create(project=self.project, request_text='Interrupted')
        TaskPlan.objects.filter(pk=plan.pk).update(created_at=timezone.now() - timedelta(days=1))
        data = self.client.get(f'{self.url}{plan.pk}/').json()['data']
        self.assertEqual(data['status'], 'FAILED')

    def test_project_archival_during_generation_keeps_history_without_tasks(self):
        """Avoid saving actionable tasks into a project archived during a model call."""
        def archive_project(*args, **kwargs):
            """Simulate another client archiving the project while the master runs."""
            Project.objects.filter(pk=self.project.pk).update(archived_at=timezone.now())
            return json.dumps(example_plan())

        with patch('django_modules.task_planning.call_model', side_effect=archive_project):
            response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['data']['errorCode'], 'PROJECT_ARCHIVED')
        self.assertFalse(Task.objects.exists())

    def test_history_pagination_project_scoping_and_archived_access(self):
        """Bound history pages and prevent reading a plan through another project."""
        for index in range(23):
            TaskPlan.objects.create(project=self.project, request_text=f'Plan {index}', status='FAILED')
        page = self.client.get(self.url).json()['data']
        self.assertEqual(len(page['items']), 20)
        self.assertEqual(page['items'][0]['request'], 'Plan 22')
        self.assertEqual(page['nextOffset'], 20)
        self.assertEqual(len(self.client.get(self.url + '?offset=20').json()['data']['items']), 3)
        self.assertEqual(self.client.get(self.url + '?offset=-1').status_code, 400)
        other = Project.objects.create(name='Other')
        self.assertEqual(self.client.get(f"/api/projects/{other.pk}/plans/{page['items'][0]['id']}/").status_code, 404)
        self.project.archived_at = timezone.now(); self.project.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.post().status_code, 404)

    def test_csrf_and_workspace_access_rules_apply_to_plans(self):
        """Require CSRF for writes and staff authentication for remote clients."""
        self.assertEqual(self.post(client=Client(enforce_csrf_checks=True)).status_code, 403)
        self.assertEqual(Client(REMOTE_ADDR='192.168.1.50').get(self.url).status_code, 403)
        staff = get_user_model().objects.create_user(username='planner-admin', is_staff=True)
        client = Client(REMOTE_ADDR='192.168.1.50'); client.force_login(staff)
        self.assertEqual(client.get(self.url).status_code, 200)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_model_validation_rejects_invalid_dependency_edits(self):
        """Protect admin edits against self-dependencies, cycles, and project/plan mismatches."""
        first = Task.objects.create(project=self.project, title='First')
        second = Task.objects.create(project=self.project, title='Second')
        other = Task.objects.create(project=Project.objects.create(name='Other'), title='Other')
        TaskDependency.objects.create(task=second, depends_on=first)
        for edge in [TaskDependency(task=first, depends_on=first), TaskDependency(task=first, depends_on=second),
                     TaskDependency(task=first, depends_on=other)]:
            with self.assertRaises(ValidationError):
                edge.full_clean()
        self.generate()
        generated = Task.objects.get(plan_key='contract')
        with self.assertRaises(ValidationError):
            TaskDependency(task=first, depends_on=generated).full_clean()
        generated.project = other.project
        with self.assertRaises(ValidationError):
            generated.full_clean()

    def test_admin_cannot_edit_or_delete_generated_plan_tasks(self):
        """Keep generated plan history immutable through Django admin controls."""
        self.generate()
        task = Task.objects.get(plan_key='test')
        request = RequestFactory().get('/admin/')
        request.user = get_user_model().objects.create_superuser('root', password='test-password')
        task_admin = admin.site._registry[Task]
        self.assertFalse(task_admin.has_change_permission(request, task))
        self.assertFalse(task_admin.has_delete_permission(request, task))
        form = TaskDependencyForm(data={'task': task.pk, 'depends_on': Task.objects.get(plan_key='contract').pk})
        self.assertFalse(form.is_valid())


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL row locks are required')
@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class TaskPlanningConcurrencyTests(TransactionTestCase):
    def test_competing_submissions_generate_once_outside_transactions(self):
        """Replay in-flight work and reject competing plans without holding network-time row locks."""
        project = Project.objects.create(name='Concurrent project')
        Agent.objects.create(name='Master', role='MASTER', ai_model=AIModel.objects.create(ollama_name='test:3b'))
        url = f'/api/projects/{project.pk}/plans/'
        body = {'id': str(uuid.uuid4()), 'request': 'Build login'}
        started, release = Event(), Event()

        def delayed_model(*args, **kwargs):
            """Hold model execution open while another client accesses its reservation."""
            self.assertFalse(connection.in_atomic_block)
            started.set()
            if not release.wait(timeout=15):
                raise AssertionError('Concurrent client did not release the model call.')
            return json.dumps(example_plan())

        def first_request():
            """Execute generation on a separate database connection."""
            close_old_connections()
            try:
                return Client().post(url, json.dumps(body), content_type='application/json')
            finally:
                connections.close_all()

        with patch('django_modules.task_planning.call_model', side_effect=delayed_model) as call:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(first_request)
                try:
                    self.assertTrue(started.wait(timeout=10))
                    replay = Client().post(url, json.dumps(body), content_type='application/json')
                    competing = Client().post(url, json.dumps({**body, 'id': str(uuid.uuid4())}), content_type='application/json')
                    self.assertEqual(replay.status_code, 202, replay.content)
                    self.assertEqual(competing.status_code, 409, competing.content)
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10).status_code, 201)
            self.assertEqual(call.call_count, 1)
        self.assertEqual(TaskPlan.objects.count(), 1)
        self.assertEqual(Task.objects.count(), 4)
