"""FR-16 additions: leases, result storage and a real HTTP worker/model round trip."""
import io
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import Client, LiveServerTestCase, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .task_execution import sign_job
from django_modules.models import AIModel, Agent, Metric, Task, TaskDependency
from .models import WorkerTaskResult
from worker import task_runner as execute
from worker import worker as original_worker
from worker.worker import DEFAULTS, Workspace


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class ExecutionApiTests(TestCase):
    def setUp(self):
        model = AIModel.objects.create(ollama_name='test:version1', max_context=8192)
        self.agent = Agent.objects.create(name='worker', ai_model=model, context_window=8192,
                                         last_heartbeat=timezone.now())
        self.task = Task.objects.create(title='Add', instructions='Write add(a,b).',
                                       assigned_agent=self.agent, status='ASSIGNED')
        tokens = override_settings(FR16_WORKER_TOKENS={str(self.agent.pk): 'test-fr16'})
        tokens.enable()
        self.addCleanup(tokens.disable)
        self.client = Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION='Bearer test-fr16')

    def post(self, suffix, data, agent=None):
        return self.client.post(f'/api/agents/{agent or self.agent.pk}/tasks/{self.task.pk}/{suffix}/',
                                json.dumps(data), content_type='application/json')

    def start(self):
        response = self.post('start', {})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['data']

    def result(self, job=None, **changes):
        job = job or self.start()
        value = {key: job[key] for key in ('lease', 'attempt')}
        value.update(status='COMPLETED', output='def add(a,b):\n return a+b', error='',
                     duration_s=2, prompt_tokens=20, output_tokens=12, model_metadata={'digest': 'fixture-model-version'})
        value.update(changes)
        return value

    def test_start_snapshots_and_replays_same_lease(self):
        first, second = self.start(), self.start()
        self.assertEqual(first['lease'], second['lease'])
        self.assertEqual(WorkerTaskResult.objects.count(), 1)
        self.task.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertEqual(self.task.status, 'IN_PROGRESS')
        self.assertEqual(self.agent.current_task_id, self.task.pk)
        self.assertEqual(first['model'], 'test:version1')

    def test_success_is_saved_once(self):
        result = self.result()
        self.assertEqual(self.post('result', result).status_code, 200)
        self.assertEqual(self.post('result', result).status_code, 200)
        self.assertEqual(Metric.objects.count(), 1)
        self.task.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertEqual(self.task.status, 'COMPLETED')
        self.assertEqual(self.agent.status, 'IDLE')
        self.assertIsNone(self.agent.current_task_id)
        self.assertEqual(WorkerTaskResult.objects.get().output, result['output'])
        self.assertEqual(self.post('result', dict(result, output='different')).status_code, 409)

    def test_failure_is_recorded(self):
        result = self.result(status='FAILED', output='', error='Model unavailable')
        self.assertEqual(self.post('result', result).status_code, 200)
        self.assertFalse(Metric.objects.get().success)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'FAILED')

    def test_other_worker_and_stale_attempt_cannot_submit(self):
        job = self.start()
        other = Agent.objects.create(name='other')
        self.assertEqual(self.post('start', {}, agent=other.pk).status_code, 403)
        self.assertEqual(self.post('result', self.result(job, attempt=2)).status_code, 409)
        self.assertEqual(self.post('result', self.result(job, lease='wrong')).status_code, 409)

    def test_deadline_is_enforced_and_persisted(self):
        result = self.result()
        WorkerTaskResult.objects.update(deadline=timezone.now() - timedelta(seconds=1))
        response = self.post('result', result)
        self.assertEqual(response.json()['data']['status'], 'FAILED')
        self.assertEqual(WorkerTaskResult.objects.get().error, 'Execution deadline exceeded.')
        self.assertFalse(Metric.objects.get().success)

    def test_stale_expiration_does_not_change_new_assignment(self):
        self.start()
        other = Agent.objects.create(name='replacement')
        self.task.refresh_from_db()
        self.task.assigned_agent = other
        self.task.save()
        WorkerTaskResult.objects.update(deadline=timezone.now() - timedelta(seconds=1))
        from .task_execution import expire_executions
        expire_executions(self.agent)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'IN_PROGRESS')
        self.assertEqual(self.task.assigned_agent_id, other.pk)

    def test_one_worker_cannot_start_two_tasks(self):
        self.start()
        other = Task.objects.create(title='Second', assigned_agent=self.agent, status='ASSIGNED')
        response = self.client.post(f'/api/agents/{self.agent.pk}/tasks/{other.pk}/start/', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(WorkerTaskResult.objects.count(), 1)

    def test_input_validation_and_dependency_gate(self):
        self.assertEqual(self.post('result', self.result(duration_s=float('nan'))).status_code, 400)
        self.assertEqual(self.post('result', self.result(output_tokens=True)).status_code, 400)
        dependency = Task.objects.create(title='prerequisite')
        TaskDependency.objects.create(task=self.task, depends_on=dependency)
        self.assertEqual(self.post('start', {}).status_code, 409)

    @override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=False)
    def test_shared_token_works_without_staff_and_is_required(self):
        anonymous = Client()
        path = f'/api/agents/{self.agent.pk}/tasks/{self.task.pk}/start/'
        self.assertEqual(anonymous.post(path, '{}', content_type='application/json').status_code, 403)
        staff = get_user_model().objects.create_user('worker-admin', is_staff=True)
        anonymous.force_login(staff)
        self.assertEqual(anonymous.post(path, '{}', content_type='application/json').status_code, 403)
        self.assertEqual(self.post('start', {}).status_code, 200)

    def test_context_guard_does_not_reserve_task(self):
        self.task.instructions = 'x' * 10000
        self.task.save()
        self.assertEqual(self.post('start', {}).status_code, 409)
        self.assertFalse(WorkerTaskResult.objects.exists())


@override_settings(FR16_WORKER_TOKENS={'1': 'test-fr16'})
class ExecutionClientTests(SimpleTestCase):
    def test_model_response_and_network_failure(self):
        job = dict(attempt=1, lease='lease', prompt='Write add(a,b)', context_window=4096,
                   model='test', options={}, timeout_s=10)
        job = sign_job(job, 1)
        config = dict(DEFAULTS, shared_token='test-fr16')
        response = json.dumps({'done': True, 'response': 'answer', 'prompt_eval_count': 8, 'eval_count': 3}).encode()
        tags = json.dumps({'models': [{'name': 'test:latest', 'digest': 'fixture-model-version'}]}).encode()
        with patch.object(execute.urllib.request, 'urlopen', side_effect=[io.BytesIO(tags), io.BytesIO(response)]):
            result = execute.generate(job, config)
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['output_tokens'], 3)
        with patch.object(execute.urllib.request, 'urlopen', side_effect=TimeoutError('timeout')):
            self.assertEqual(execute.generate(job, config)['status'], 'FAILED')

    def test_tampered_task_never_reaches_ollama(self):
        job = dict(attempt=1, lease='lease', prompt='fake master job', context_window=4096,
                   model='test', options={}, timeout_s=10, signature='wrong')
        with patch.object(execute.urllib.request, 'urlopen') as model:
            result = execute.generate(job, dict(DEFAULTS, shared_token='test-fr16'))
        self.assertEqual(result['status'], 'FAILED')
        self.assertIn('signature', result['error'])
        model.assert_not_called()

    def test_outbox_survives_failed_delivery(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'pending.json'
            execute.save_pending(path, '1', {'status': 'COMPLETED'})
            workspace = Mock()
            workspace.request.side_effect = OSError('connection lost')
            with self.assertRaises(OSError):
                execute.deliver(workspace, '1', path)
            self.assertTrue(path.exists())
            workspace.request.side_effect = None
            workspace.request.return_value = {'data': {'status': 'COMPLETED'}}
            execute.deliver(workspace, '1', path)
            self.assertFalse(path.exists())


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class WorkerExecutionLiveTests(LiveServerTestCase):
    host = '127.0.0.1'

    def test_worker_fetches_runs_reports_and_keeps_heartbeat_alive(self):
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        model = AIModel.objects.create(ollama_name='stub:1')
        agent = Agent.objects.create(name='worker', ai_model=model, context_window=8192)
        task = Task.objects.create(title='Add', assigned_agent=agent, status='ASSIGNED', attempts=1)
        seen = []

        class Model(BaseHTTPRequestHandler):
            def reply(self, data):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.reply({'models': [{'name': 'stub:1', 'digest': 'stub-digest'}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append(body)
                time.sleep(.15)
                self.reply({'done': True, 'response': 'def add(a,b):\n return a+b',
                            'prompt_eval_count': 20, 'eval_count': 12})

            def log_message(self, *args):
                pass

        class ObservedWorkspace(Workspace):
            def request(self, method, path, data=None, form=False):
                if data and data.get('status') == 'BUSY':
                    seen.append('busy heartbeat')
                return super().request(method, path, data, form)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = dict(DEFAULTS, agent_id=agent.pk, server_url=self.live_server_url,
                          ollama_url=f'http://127.0.0.1:{server.server_port}', interval=.03, shared_token='test-fr16')
            with TemporaryDirectory() as folder, patch.object(execute, 'Workspace', ObservedWorkspace), patch.object(original_worker, 'load_config', return_value=config), override_settings(FR16_WORKER_TOKENS={str(agent.pk): 'test-fr16'}):
                args = ['--config', str(Path(folder) / 'config.json'), '--execute', '--once']
                self.assertEqual(original_worker.main(args), 0)
                # The same existing worker must also process a PENDING task
                # chosen by the unchanged main FR-14 distributor.
                from .capabilities import save_scores
                save_scores(agent, {'coding': 80, 'reasoning': 80, 'context': 80, 'speed': 80})
                pending = Task.objects.create(title='Automatically selected', status='PENDING')
                self.assertEqual(original_worker.main(args), 0)
                pending.refresh_from_db()
                self.assertEqual(pending.status, 'COMPLETED')
                self.assertEqual(pending.assigned_agent_id, agent.pk)
            task.refresh_from_db()
            self.assertEqual(task.status, 'COMPLETED')
            self.assertEqual(WorkerTaskResult.objects.get(task=task).output, 'def add(a,b):\n return a+b')
            self.assertIn('busy heartbeat', seen)
            self.assertEqual(Metric.objects.count(), 2)
            self.assertEqual(WorkerTaskResult.objects.get(task=task).result['model_metadata']['digest'], 'stub-digest')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
