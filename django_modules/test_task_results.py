"""FR-18: project tasks are asked for JSON, checked on arrival, stored as artifacts and committed."""
import io
import json
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipIf
from unittest.mock import patch

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from worker import task_runner as execute
from worker.worker import DEFAULTS
from .models import (AIModel, Agent, BenchmarkCase, BenchmarkRun, CodeArtifact, Metric, Project, Task,
                     TaskDependency, TaskOutputConflict, TaskPlan, WorkerTaskResult)
from .result_format import RESULT_SCHEMA
from .task_execution import sign_job
from .task_results import commit_artifact, needs_files


def answer(*paths, summary='Built it.', notes=''):
    """A model answer in the agreed JSON shape."""
    return json.dumps({'files': [{'path': p, 'content': f'# {p}\n'} for p in paths],
                       'summary': summary, 'notes': notes})


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class ProjectTaskResultTests(TestCase):
    def setUp(self):
        model = AIModel.objects.create(ollama_name='test:version1', max_context=8192)
        self.agent = Agent.objects.create(name='Coder-1', ai_model=model, context_window=8192,
                                         last_heartbeat=timezone.now())
        self.project = Project.objects.create(name='Book Shop')
        self.task = self.make_task('Build login API')
        self.repo = TemporaryDirectory()
        self.addCleanup(self.repo.cleanup)
        for change in (override_settings(FR16_WORKER_TOKENS={str(self.agent.pk): 'test-fr16'}),
                       override_settings(GENERATED_REPO_DIR=self.repo.name)):
            change.enable()
            self.addCleanup(change.disable)
        self.client = Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION='Bearer test-fr16')

    def make_task(self, title, task_type='BACKEND', project=True):
        return Task.objects.create(project=self.project if project else None, type=task_type, title=title,
                                   instructions='Do it.', assigned_agent=self.agent, status='ASSIGNED')

    def post(self, task, suffix, data):
        return self.client.post(f'/api/agents/{self.agent.pk}/tasks/{task.pk}/{suffix}/',
                                json.dumps(data), content_type='application/json')

    def start(self, task=None):
        response = self.post(task or self.task, 'start', {})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['data']

    def finish(self, output, task=None, commit=True):
        """Start the task, send a completed result, run the after-commit Git step; return the response data."""
        task = task or self.task
        job = self.start(task)
        result = dict(attempt=job['attempt'], lease=job['lease'], status='COMPLETED', output=output, error='',
                      duration_s=2, prompt_tokens=20, output_tokens=12, model_metadata={'digest': 'fixture'})
        with self.captureOnCommitCallbacks(execute=commit):
            response = self.post(task, 'result', result)
        self.assertEqual(response.status_code, 200, response.content)
        task.refresh_from_db()
        return response.json()['data'], result

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo.name, capture_output=True, text=True, check=True).stdout.strip()

    # --- Step 2: what the worker is asked for ---------------------------------

    def test_project_task_job_asks_for_json_and_names_the_folders(self):
        job = self.start()
        self.assertEqual(job['format'], RESULT_SCHEMA)
        self.assertIn('Answer with JSON only', job['prompt'])
        self.assertIn('Put every file under: backend/', job['prompt'])

    def test_task_without_project_keeps_plain_text(self):
        job = self.start(self.make_task('Loose task', project=False))
        self.assertNotIn('format', job)
        self.assertNotIn('Answer with JSON only', job['prompt'])

    def test_benchmark_tasks_keep_plain_text(self):
        run = BenchmarkRun.objects.create(agent=self.agent, model_name='test:version1')
        BenchmarkCase.objects.create(run=run, task=self.task, metric='coding', case_id='add')
        self.task.refresh_from_db()
        self.assertFalse(needs_files(self.task))
        self.assertNotIn('format', self.start())

    def test_format_is_part_of_the_signed_job(self):
        job = self.start()
        unsigned = {key: value for key, value in job.items() if key != 'signature'}
        self.assertEqual(sign_job(unsigned, self.agent.pk)['signature'], job['signature'])

    # --- Step 3: what happens to the answer ------------------------------------

    def test_valid_answer_is_stored_as_an_artifact(self):
        data, _ = self.finish(answer('backend/app.py', 'backend/models.py', notes='Uses SQLite.'), commit=False)
        self.assertEqual(data['status'], 'COMPLETED')
        self.assertEqual(self.task.status, 'COMPLETED')
        artifact = CodeArtifact.objects.get()
        self.assertEqual((artifact.task, artifact.agent, artifact.attempt), (self.task, self.agent, 1))
        self.assertEqual([f['path'] for f in artifact.files['files']], ['backend/app.py', 'backend/models.py'])
        self.assertEqual(artifact.files['summary'], 'Built it.')
        self.assertEqual(artifact.notes, 'Uses SQLite.')
        self.assertTrue(Metric.objects.get().success)

    @skipIf(shutil.which('git') is None, 'git is not installed')
    def test_valid_answer_is_committed_to_the_project_branch(self):
        self.finish(answer('backend/app.py'))
        branch = f'project/{self.project.pk}-book-shop'
        artifact = CodeArtifact.objects.get()
        self.assertEqual(artifact.files['branch'], branch)
        self.assertEqual(artifact.files['commit'], self.git('rev-parse', branch))
        self.assertEqual(artifact.files['commit_error'], '')
        self.assertEqual(self.git('show', f'{branch}:backend/app.py'), '# backend/app.py')
        self.assertEqual(self.git('log', '-1', '--format=%s', branch),
                         f'[task {self.task.pk}] Build login API (Coder-1, attempt 1)')
        self.project.refresh_from_db()
        self.assertEqual(self.project.git_branch, branch)

    @skipIf(shutil.which('git') is None, 'git is not installed')
    def test_tasks_of_one_project_stack_up_on_one_branch(self):
        self.finish(answer('backend/app.py'))
        self.agent.refresh_from_db()
        second = self.make_task('Write tests', task_type='TESTING')
        self.finish(answer('tests/test_app.py'), task=second)
        branch = f'project/{self.project.pk}-book-shop'
        self.assertEqual(self.git('log', '--format=%s', f'main..{branch}').count('[task '), 2)
        self.assertEqual(sorted(self.git('ls-tree', '-r', '--name-only', branch).splitlines()),
                         ['backend/app.py', 'tests/test_app.py'])

    @skipIf(shutil.which('git') is None, 'git is not installed')
    def test_conflicting_parallel_outputs_queue_resolver_and_hold_the_file(self):
        first = json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'version one'}],
                            'summary': 'First implementation'})
        second = json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'version two'}],
                             'summary': 'Second implementation'})
        self.finish(first)
        downstream = Task.objects.create(project=self.project, title='Join both branches', status='PENDING')
        TaskDependency.objects.create(task=downstream, depends_on=self.task)
        other = self.make_task('Build another feature')

        self.finish(second, task=other)

        conflict = TaskOutputConflict.objects.get()
        resolver = conflict.resolver_task
        self.assertEqual(conflict.status, 'OPEN')
        self.assertEqual((resolver.type, resolver.status), ('INTEGRATION', 'PENDING'))
        self.assertSetEqual(set(conflict.artifacts.values_list('task_id', flat=True)), {self.task.pk, other.pk})
        self.assertSetEqual(set(resolver.dependencies.values_list('depends_on_id', flat=True)),
                            {self.task.pk, other.pk})
        self.assertTrue(downstream.dependencies.filter(depends_on=resolver).exists())
        branch = f'project/{self.project.pk}-book-shop'
        self.assertEqual(self.git('show', f'{branch}:backend/shared.py'), 'version one')

        resolver.assigned_agent = self.agent
        resolver.status = 'ASSIGNED'
        resolver.save(update_fields=['assigned_agent', 'status'])
        merged = json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'merged version'}],
                             'summary': 'Resolved both changes'})
        self.finish(merged, task=resolver)
        conflict.refresh_from_db()
        self.assertEqual(conflict.status, 'RESOLVED')
        self.assertIn('version one', WorkerTaskResult.objects.get(task=resolver).prompt)
        self.assertIn('version two', WorkerTaskResult.objects.get(task=resolver).prompt)
        self.assertNotIn('Direct prerequisite outputs:', WorkerTaskResult.objects.get(task=resolver).prompt)
        self.assertEqual(self.git('show', f'{branch}:backend/shared.py'), 'merged version')

    def test_sequential_changes_to_the_same_file_are_not_conflicts(self):
        first = json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'initial'}], 'summary': 'First'})
        second = json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'updated'}], 'summary': 'Next'})
        self.finish(first)
        other = self.make_task('Continue the implementation')
        TaskDependency.objects.create(task=other, depends_on=self.task)

        self.finish(second, task=other)

        self.assertFalse(TaskOutputConflict.objects.exists())

    def test_resolver_must_return_only_the_conflicted_file(self):
        self.finish(json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'one'}], 'summary': 'First'}))
        other = self.make_task('Build another feature')
        self.finish(json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'two'}], 'summary': 'Second'}),
                    task=other)
        conflict = TaskOutputConflict.objects.get()
        resolver = conflict.resolver_task
        resolver.assigned_agent = self.agent
        resolver.status = 'ASSIGNED'
        resolver.save(update_fields=['assigned_agent', 'status'])

        data, _ = self.finish(answer('backend/other.py'), task=resolver)

        self.assertEqual(data['status'], 'FAILED')
        self.assertIn('exactly the complete file', data['error'])
        conflict.refresh_from_db()
        self.assertEqual(conflict.status, 'FAILED')

    def test_identical_parallel_outputs_do_not_create_a_conflict(self):
        output = json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'same'}], 'summary': 'Same'})
        self.finish(output)
        other = self.make_task('Repeat the same implementation')

        self.finish(output, task=other)

        self.assertFalse(TaskOutputConflict.objects.exists())

    def test_planned_conflict_creates_resolver_in_the_same_plan(self):
        plan = TaskPlan.objects.create(project=self.project, request_text='Build features',
                                       model_name='planner', agent_name='master')
        first = Task.objects.create(project=self.project, plan=plan, plan_key='first', title='First',
                                    assigned_agent=self.agent, status='ASSIGNED')
        second = Task.objects.create(project=self.project, plan=plan, plan_key='second', title='Second',
                                     assigned_agent=self.agent, status='ASSIGNED')
        self.finish(json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'one'}], 'summary': 'First'}),
                    task=first)
        self.finish(json.dumps({'files': [{'path': 'backend/shared.py', 'content': 'two'}], 'summary': 'Second'}),
                    task=second)

        conflict = TaskOutputConflict.objects.get()
        self.assertEqual(conflict.plan, plan)
        self.assertEqual(conflict.resolver_task.plan, plan)
        self.assertTrue(conflict.resolver_task.plan_key.startswith('fr24-'))
        self.assertEqual(conflict.resolver_task.dependencies.count(), 2)

    def test_answer_that_is_not_json_fails_the_attempt_with_the_reason(self):
        data, _ = self.finish('Sure! Here is the code: def login(): ...')
        self.assertEqual(data['status'], 'FAILED')
        self.assertIn('not valid JSON', data['error'])
        self.assertEqual(self.task.status, 'FAILED')
        execution = WorkerTaskResult.objects.get()
        self.assertEqual(execution.status, 'FAILED')
        self.assertTrue(execution.error.startswith('Invalid result:'))
        self.assertEqual(execution.output, 'Sure! Here is the code: def login(): ...')  # kept for debugging
        self.assertFalse(CodeArtifact.objects.exists())
        self.assertFalse(Metric.objects.get().success)
        self.agent.refresh_from_db()
        self.assertEqual((self.agent.status, self.agent.current_task_id), ('IDLE', None))

    def test_file_outside_the_task_types_folders_fails_the_attempt(self):
        data, _ = self.finish(answer('backend/app.py', 'frontend/App.jsx'))
        self.assertEqual(data['status'], 'FAILED')
        self.assertIn('frontend/App.jsx', data['error'])
        self.assertFalse(CodeArtifact.objects.exists())

    def test_sending_the_same_invalid_result_again_is_harmless(self):
        data, result = self.finish('not json')
        again = self.post(self.task, 'result', result)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['data']['status'], 'FAILED')
        self.assertEqual(Metric.objects.count(), 1)

    def test_task_without_project_still_accepts_plain_text(self):
        loose = self.make_task('Loose task', project=False)
        data, _ = self.finish('def add(a, b): return a + b', task=loose)
        self.assertEqual(data['status'], 'COMPLETED')
        self.assertFalse(CodeArtifact.objects.exists())

    def test_committing_can_be_turned_off(self):
        with override_settings(GENERATED_REPO_DIR=''):
            self.finish(answer('backend/app.py'))
        artifact = CodeArtifact.objects.get()
        self.assertIsNone(artifact.files['commit'])
        self.assertEqual(self.task.status, 'COMPLETED')
        self.assertEqual(os.listdir(self.repo.name), [])  # no repo was created

    def test_failed_commit_keeps_the_task_completed_and_records_the_error(self):
        self.finish(answer('backend/app.py'), commit=False)
        artifact = CodeArtifact.objects.get()
        with patch('django_modules.task_results.commit_files', side_effect=OSError('disk full')):
            self.assertIsNone(commit_artifact(artifact.pk))
        artifact.refresh_from_db()
        self.assertEqual(artifact.files['commit_error'], 'disk full')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'COMPLETED')


@override_settings(FR16_WORKER_TOKENS={'1': 'test-fr16'})
class WorkerFormatTests(SimpleTestCase):
    def run_job(self, **extra):
        job = sign_job(dict(attempt=1, lease='lease', prompt='Build it', context_window=4096,
                            model='test', options={}, timeout_s=10, **extra), 1)
        tags = json.dumps({'models': [{'name': 'test:latest', 'digest': 'fixture'}]}).encode()
        reply = json.dumps({'done': True, 'response': answer('backend/app.py'), 'eval_count': 3}).encode()
        with patch.object(execute.urllib.request, 'urlopen', side_effect=[io.BytesIO(tags), io.BytesIO(reply)]) as call:
            result = execute.generate(job, dict(DEFAULTS, shared_token='test-fr16'))
        return result, json.loads(call.call_args_list[1].args[0].data)

    def test_worker_passes_the_format_to_ollama(self):
        result, body = self.run_job(format=RESULT_SCHEMA)
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(body['format'], RESULT_SCHEMA)

    def test_worker_sends_no_format_for_plain_text_jobs(self):
        _, body = self.run_job()
        self.assertNotIn('format', body)
