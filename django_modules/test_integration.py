"""FR-20: the integration step merges every task output of a plan into one runnable project."""
import json
import shutil
import subprocess
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest import skipIf
from unittest.mock import patch

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .integration import (check_files, integrate_plan, integrate_when_complete, merge_outputs, plan_order,
                          run_commands, scaffold)
from .models import AIModel, Agent, CodeArtifact, Project, ProjectIntegration, Task, TaskDependency, TaskPlan
from .workspace_api import APIError

HAS_GIT = shutil.which('git') is not None
APP = 'from flask import Flask\napp = Flask(__name__)\n\nif __name__ == "__main__":\n    app.run()\n'


class MergeRuleTests(SimpleTestCase):
    def test_plan_order_puts_prerequisites_first_and_keeps_id_order_for_ties(self):
        edges = {1: set(), 2: {4}, 3: {1}, 4: set()}
        self.assertEqual(plan_order([1, 2, 3, 4], edges), [1, 3, 4, 2])

    def test_plan_order_rejects_a_cycle(self):
        with self.assertRaises(ValueError):
            plan_order([1, 2], {1: {2}, 2: {1}})

    def test_independent_tasks_are_combined(self):
        files, conflicts, overrides = merge_outputs(
            [(1, 'api', [{'path': 'backend/app.py', 'content': 'a'}]),
             (2, 'ui', [{'path': 'frontend/index.html', 'content': 'b'}])], {})
        self.assertEqual({path: entry['task'] for path, entry in files.items()},
                         {'backend/app.py': 'api', 'frontend/index.html': 'ui'})
        self.assertEqual((conflicts, overrides), ([], []))

    def test_a_dependent_task_may_replace_a_prerequisites_file(self):
        # glue depends on db, which depends on api: the transitive dependent wins.
        edges = {1: set(), 2: {1}, 3: {2}}
        files, conflicts, overrides = merge_outputs(
            [(1, 'api', [{'path': 'backend/app.py', 'content': 'v1'}]),
             (2, 'db', [{'path': 'backend/models.py', 'content': 'm'}]),
             (3, 'glue', [{'path': 'backend/app.py', 'content': 'v2'}])], edges)
        self.assertEqual(files['backend/app.py']['content'], 'v2')
        self.assertEqual(conflicts, [])
        self.assertEqual(overrides, [{'path': 'backend/app.py', 'from': 'api', 'to': 'glue'}])

    def test_independent_tasks_writing_one_path_differently_conflict(self):
        files, conflicts, _ = merge_outputs(
            [(1, 'api', [{'path': 'backend/app.py', 'content': 'a'}]),
             (2, 'db', [{'path': 'backend/app.py', 'content': 'b'}])], {1: set(), 2: set()})
        self.assertEqual(conflicts, [{'path': 'backend/app.py', 'tasks': ['api', 'db']}])
        self.assertEqual(files['backend/app.py']['task'], 'api')

    def test_identical_content_is_not_a_conflict(self):
        _, conflicts, overrides = merge_outputs(
            [(1, 'api', [{'path': 'backend/requirements.txt', 'content': 'flask\n'}]),
             (2, 'db', [{'path': 'backend/requirements.txt', 'content': 'flask\n'}])], {})
        self.assertEqual((conflicts, overrides), ([], []))

    def test_checks_find_syntax_errors_bad_json_and_broken_links(self):
        problems = check_files({
            'backend/app.py': 'def broken(:\n',
            'frontend/package.json': '{"name": ',
            'frontend/index.html': '<script src="app.js"></script><link href="style.css">'
                                   '<a href="https://example.com">x</a><img src="{{ logo }}"><a href="#top">t</a>',
            'frontend/style.css': 'body {}',
        })
        self.assertEqual(len(problems), 3, problems)
        self.assertIn('backend/app.py: Python syntax error (line 1)', problems[0])
        self.assertIn("frontend/index.html: links to 'app.js'", problems[1])
        self.assertIn('frontend/package.json: invalid JSON', problems[2])

    def test_checks_resolve_relative_links_between_folders(self):
        self.assertEqual(check_files({'frontend/pages/a.html': '<script src="../js/app.js"></script>',
                                      'frontend/js/app.js': ''}), [])

    def test_checks_never_execute_generated_code(self):
        with patch('builtins.exec') as run:
            self.assertEqual(check_files({'backend/app.py': 'raise SystemExit(1)\n'}), [])
        run.assert_not_called()

    def test_run_commands_for_a_flask_api_with_static_frontend_and_tests(self):
        run = run_commands({'backend/app.py': APP, 'backend/requirements.txt': 'flask\n',
                            'frontend/index.html': '', 'tests/test_app.py': ''})
        self.assertEqual(run, {'install': ['python -m pip install -r requirements.txt'],
                               'start': ['python backend/app.py', 'python -m http.server 8080 --directory frontend'],
                               'test': ['python -m pytest tests']})

    def test_run_commands_for_django_and_npm(self):
        run = run_commands({'backend/manage.py': '', 'backend/main.py': '',
                            'frontend/package.json': json.dumps({'scripts': {'dev': 'vite'}}),
                            'frontend/index.html': ''})
        self.assertEqual(run['install'], ['npm --prefix frontend install'])
        self.assertEqual(run['start'], ['npm --prefix frontend run dev', 'python backend/manage.py runserver'])
        self.assertEqual(run['test'], [])

    def test_scaffold_writes_readme_requirements_and_a_stable_manifest(self):
        files = {'backend/app.py': {'content': APP, 'task': 'api', 'taskId': 1},
                 'backend/requirements.txt': {'content': 'flask\n', 'task': 'api', 'taskId': 1},
                 'tests/requirements.txt': {'content': 'pytest\n', 'task': 'qa', 'taskId': 2}}
        tasks = [{'key': 'api', 'title': 'API', 'type': 'BACKEND', 'files': ['backend/app.py']},
                 {'key': 'qa', 'title': 'Tests', 'type': 'TESTING', 'files': ['tests/requirements.txt']}]
        plan = {'id': 'p1', 'title': 'Shop', 'summary': 'A shop.'}
        run = run_commands({path: entry['content'] for path, entry in files.items()})
        first = scaffold('Book Shop', plan, tasks, files, run, [])
        self.assertEqual(first, scaffold('Book Shop', plan, tasks, files, run, []))
        self.assertEqual(sorted(first), ['README.md', 'integration.json', 'requirements.txt'])
        self.assertEqual(first['requirements.txt'].splitlines()[1:],
                         ['-r backend/requirements.txt', '-r tests/requirements.txt'])
        self.assertIn('# Book Shop', first['README.md'])
        self.assertIn('python backend/app.py', first['README.md'])
        manifest = json.loads(first['integration.json'])
        self.assertEqual(manifest['files'][0], {'path': 'backend/app.py', 'task': 'api'})
        self.assertEqual(manifest['run'], run)


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class IntegrationStepTests(TestCase):
    def setUp(self):
        model = AIModel.objects.create(ollama_name='test:version1', max_context=8192)
        self.agent = Agent.objects.create(name='Coder-1', ai_model=model, context_window=8192,
                                         last_heartbeat=timezone.now())
        self.project = Project.objects.create(name='Book Shop')
        self.plan = TaskPlan.objects.create(project=self.project, request_text='Build a shop', title='Shop',
                                            summary='An online book shop.', status='READY', model_name='m',
                                            agent_name='Master')
        self.repo = TemporaryDirectory()
        self.addCleanup(self.repo.cleanup)
        change = override_settings(GENERATED_REPO_DIR=self.repo.name)
        change.enable()
        self.addCleanup(change.disable)
        self.client = Client(enforce_csrf_checks=False)
        self.url = f'/api/projects/{self.project.pk}/plans/{self.plan.pk}/integration/'

    def task(self, key, task_type, files=None, depends_on=(), status='COMPLETED'):
        task = Task.objects.create(project=self.project, plan=self.plan, plan_key=key, title=key.title(),
                                   type=task_type, status=status, assigned_agent=self.agent)
        for prerequisite in depends_on:
            TaskDependency.objects.create(task=task, depends_on=prerequisite)
        if files is not None:
            self.artifact(task, files)
        return task

    def artifact(self, task, files, attempt=1):
        return CodeArtifact.objects.create(task=task, agent=self.agent, attempt=attempt, files={
            'files': [{'path': path, 'content': content} for path, content in files.items()],
            'summary': '', 'branch': '', 'commit': None, 'commit_error': ''})

    def shop(self):
        """api -> (ui, qa): a small runnable plan."""
        api = self.task('api', 'BACKEND', {'backend/app.py': APP, 'backend/requirements.txt': 'flask\n'})
        ui = self.task('ui', 'FRONTEND', {'frontend/index.html': '<script src="app.js"></script>',
                                         'frontend/app.js': 'fetch("/api/books")\n'}, depends_on=[api])
        qa = self.task('qa', 'TESTING', {'tests/test_app.py': 'def test_ok():\n    assert True\n'}, depends_on=[api])
        return api, ui, qa

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo.name, capture_output=True, text=True,
                              check=True).stdout.strip()

    @skipIf(not HAS_GIT, 'git is not installed')
    def test_completed_plan_is_merged_and_committed_as_one_project(self):
        self.shop()
        response = self.client.post(self.url, '{}', content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()['data']
        branch = f'project/{self.project.pk}-book-shop'
        self.assertEqual((data['status'], data['branch'], data['problems'], data['conflicts']),
                         ('COMPLETED', branch, [], []))
        self.assertEqual(data['commit'], self.git('rev-parse', branch))
        self.assertEqual(sorted(self.git('ls-tree', '-r', '--name-only', branch).splitlines()),
                         ['README.md', 'backend/app.py', 'backend/requirements.txt', 'frontend/app.js',
                          'frontend/index.html', 'integration.json', 'requirements.txt', 'tests/test_app.py'])
        self.assertTrue(self.git('log', '-1', '--format=%s', branch).startswith('[integration] Shop'))
        self.assertEqual(data['run']['start'], ['python backend/app.py',
                                                'python -m http.server 8080 --directory frontend'])
        manifest = json.loads(self.git('show', f'{branch}:integration.json'))
        self.assertEqual([task['key'] for task in manifest['tasks']], ['api', 'ui', 'qa'])
        self.assertEqual(ProjectIntegration.objects.get().files[1]['task'], 'api')

    @skipIf(not HAS_GIT, 'git is not installed')
    def test_integrating_the_same_outputs_again_reuses_the_record(self):
        self.shop()
        first, created = integrate_plan(self.plan.pk)
        again, created_again = integrate_plan(self.plan.pk)
        self.assertEqual((created, created_again, again.pk), (True, False, first.pk))
        response = self.client.post(self.url, '{}', content_type='application/json')
        self.assertEqual(response.status_code, 200)

    @skipIf(not HAS_GIT, 'git is not installed')
    def test_a_newer_attempt_of_a_task_is_integrated_again(self):
        api, _, _ = self.shop()
        first, _ = integrate_plan(self.plan.pk)
        self.artifact(api, {'backend/app.py': APP + '# v2\n', 'backend/requirements.txt': 'flask\n'}, attempt=2)
        second, created = integrate_plan(self.plan.pk)
        self.assertTrue(created)
        self.assertNotEqual(first.commit, second.commit)
        self.assertIn('# v2', self.git('show', f'{second.branch}:backend/app.py'))

    def test_incomplete_plan_is_rejected_with_the_unfinished_tasks(self):
        api = self.task('api', 'BACKEND', {'backend/app.py': APP})
        self.task('ui', 'FRONTEND', depends_on=[api], status='PENDING')
        response = self.client.post(self.url, '{}', content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'PLAN_INCOMPLETE')
        self.assertIn('ui', response.json()['error'])
        self.assertFalse(ProjectIntegration.objects.exists())

    def test_plan_that_is_not_saved_is_rejected(self):
        self.plan.status = 'FAILED'
        self.plan.save(update_fields=['status'])
        with self.assertRaises(APIError) as caught:
            integrate_plan(self.plan.pk)
        self.assertEqual(caught.exception.code, 'PLAN_NOT_READY')

    def test_conflict_fails_the_integration_and_commits_nothing(self):
        self.task('api', 'BACKEND', {'backend/app.py': APP})
        self.task('db', 'DATABASE', {'backend/app.py': 'x = 1\n'})
        integration, _ = integrate_plan(self.plan.pk)
        self.assertEqual(integration.status, 'FAILED')
        self.assertEqual(integration.conflicts, [{'path': 'backend/app.py', 'tasks': ['api', 'db']}])
        self.assertEqual(integration.commit, '')
        self.assertIsNotNone(integration.completed_at)

    def test_project_without_an_entry_point_is_not_runnable(self):
        self.task('docs', 'DOCUMENTATION', {'docs/guide.md': '# Guide\n'})
        integration, _ = integrate_plan(self.plan.pk)
        self.assertEqual(integration.status, 'FAILED')
        self.assertIn('No start command found', integration.problems[0])

    def test_task_without_files_is_a_warning_not_a_failure(self):
        api = self.task('api', 'BACKEND', {'backend/app.py': APP})
        self.task('review', 'REVIEW', depends_on=[api])
        with override_settings(GENERATED_REPO_DIR=''):
            integration, _ = integrate_plan(self.plan.pk)
        self.assertEqual(integration.status, 'COMPLETED')
        self.assertEqual(integration.warnings, ['Task review produced no files.'])
        self.assertEqual((integration.commit, integration.branch), ('', ''))

    @skipIf(not HAS_GIT, 'git is not installed')
    def test_failed_commit_is_recorded_and_can_be_retried(self):
        self.shop()
        with patch('django_modules.integration.commit_files', side_effect=OSError('disk full')):
            failed, _ = integrate_plan(self.plan.pk)
        self.assertEqual((failed.status, failed.error), ('FAILED', 'Commit failed: disk full'))
        retried, created = integrate_plan(self.plan.pk)
        self.assertTrue(created)
        self.assertEqual(retried.status, 'COMPLETED')

    def test_interrupted_run_is_retried_after_it_goes_stale(self):
        self.shop()
        with override_settings(GENERATED_REPO_DIR=''):
            first, _ = integrate_plan(self.plan.pk)
            ProjectIntegration.objects.filter(pk=first.pk).update(
                status='RUNNING', created_at=timezone.now() - timedelta(minutes=11))
            second, created = integrate_plan(self.plan.pk)
        self.assertTrue(created)
        self.assertNotEqual(first.pk, second.pk)

    def test_plan_detail_and_get_show_the_latest_integration(self):
        self.shop()
        self.assertIsNone(self.client.get(self.url).json()['data'])
        with override_settings(GENERATED_REPO_DIR=''):
            integration, _ = integrate_plan(self.plan.pk)
        self.assertEqual(self.client.get(self.url).json()['data']['id'], str(integration.pk))
        detail = self.client.get(f'/api/projects/{self.project.pk}/plans/{self.plan.pk}/').json()['data']
        self.assertEqual(detail['integration']['status'], 'COMPLETED')

    def test_post_requires_csrf(self):
        self.shop()
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.url, '{}', content_type='application/json').status_code, 403)

    def test_automatic_step_waits_for_the_last_task(self):
        api = self.task('api', 'BACKEND', {'backend/app.py': APP})
        ui = self.task('ui', 'FRONTEND', depends_on=[api], status='IN_PROGRESS')
        with override_settings(GENERATED_REPO_DIR=''):
            self.assertIsNone(integrate_when_complete(self.plan.pk))
            ui.status = 'COMPLETED'
            ui.save(update_fields=['status'])
            self.artifact(ui, {'frontend/index.html': '<h1>Shop</h1>'})
            self.assertEqual(integrate_when_complete(self.plan.pk).status, 'COMPLETED')

    def test_automatic_step_never_raises(self):
        self.shop()
        with patch('django_modules.integration.integrate_plan', side_effect=RuntimeError('boom')):
            self.assertIsNone(integrate_when_complete(self.plan.pk))


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class WorkerResultTriggersIntegrationTests(TestCase):
    """End to end through the FR-16 worker API: the last result of a plan integrates it."""

    def setUp(self):
        model = AIModel.objects.create(ollama_name='test:version1', max_context=8192)
        self.agent = Agent.objects.create(name='Coder-1', ai_model=model, context_window=8192,
                                         last_heartbeat=timezone.now())
        self.project = Project.objects.create(name='Book Shop')
        self.plan = TaskPlan.objects.create(project=self.project, request_text='Build', title='Shop',
                                            status='READY', model_name='m', agent_name='Master')
        self.api = Task.objects.create(project=self.project, plan=self.plan, plan_key='api', title='API',
                                       type='BACKEND', status='ASSIGNED', assigned_agent=self.agent)
        self.qa = Task.objects.create(project=self.project, plan=self.plan, plan_key='qa', title='Tests',
                                      type='TESTING', status='PENDING')
        TaskDependency.objects.create(task=self.qa, depends_on=self.api)
        for change in (override_settings(FR16_WORKER_TOKENS={str(self.agent.pk): 'test-fr16'}),
                       override_settings(GENERATED_REPO_DIR='')):
            change.enable()
            self.addCleanup(change.disable)
        self.client = Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION='Bearer test-fr16')

    def run_task(self, task, files):
        url = f'/api/agents/{self.agent.pk}/tasks/{task.pk}'
        job = self.client.post(f'{url}/start/', '{}', content_type='application/json').json()['data']
        output = json.dumps({'files': [{'path': p, 'content': c} for p, c in files.items()], 'summary': 'Done.'})
        result = dict(attempt=job['attempt'], lease=job['lease'], status='COMPLETED', output=output, error='',
                      duration_s=1, prompt_tokens=1, output_tokens=1, model_metadata={'digest': 'fixture'})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f'{url}/result/', json.dumps(result), content_type='application/json')
        self.assertEqual(response.json()['data']['status'], 'COMPLETED', response.content)

    def test_last_result_of_the_plan_integrates_it(self):
        self.run_task(self.api, {'backend/app.py': APP})
        self.assertFalse(ProjectIntegration.objects.exists())
        Task.objects.filter(pk=self.qa.pk).update(status='ASSIGNED', assigned_agent=self.agent)
        self.run_task(self.qa, {'tests/test_app.py': 'def test_ok():\n    assert True\n'})
        integration = ProjectIntegration.objects.get()
        self.assertEqual(integration.status, 'COMPLETED')
        self.assertEqual(integration.run['test'], ['python -m pytest tests'])
        self.assertEqual([item['path'] for item in integration.files],
                         ['README.md', 'backend/app.py', 'integration.json', 'tests/test_app.py'])
