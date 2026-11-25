"""Tests for FR-23: failed tasks are retried and reassigned (task_retry.py)."""
import json
from datetime import timedelta

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .capabilities import save_scores
from .models import (AIModel, Agent, BenchmarkCase, BenchmarkRun, Metric, Project, Task, TaskOutputConflict,
                     WorkerTaskResult)
from .task_retry import (OFFLINE_ERROR, after_failure, can_retry, release_abandoned_tasks, retry_exclusions,
                         retry_failed_task)

SCORES = {'coding': 80, 'reasoning': 80, 'context': 80, 'speed': 80}
BETTER = {'coding': 95, 'reasoning': 95, 'context': 95, 'speed': 95}


class RetryRuleTests(SimpleTestCase):
    """The pure rules, with unsaved objects and no database."""

    def setUp(self):
        self.now = timezone.now()
        self.a, self.b = self.agent(1), self.agent(2)
        self.task = Task(pk=10, title='Build the API')
        self.scores = {1: SCORES, 2: SCORES}

    def agent(self, pk, **fields):
        return Agent(pk=pk, status='IDLE', last_heartbeat=timezone.now(), context_window=8192, **fields)

    def exclusions(self, candidates, failed, scores=None):
        return retry_exclusions([self.task], candidates, self.now, scores or self.scores, failed)[10]

    def test_attempts_are_limited(self):
        self.assertTrue(can_retry(1, 3))
        self.assertTrue(can_retry(2, 3))
        self.assertFalse(can_retry(3, 3))
        self.assertFalse(can_retry(1, 1))

    def test_agent_that_failed_is_excluded_while_another_can_take_the_task(self):
        self.assertEqual(self.exclusions([self.a, self.b], {10: {1}}), {1})

    def test_exclusion_is_dropped_when_every_agent_failed(self):
        self.assertEqual(self.exclusions([self.a, self.b], {10: {1, 2}}), set())

    def test_exclusion_is_dropped_when_the_only_other_agent_is_offline(self):
        offline = self.agent(2, )
        offline.status = 'OFFLINE'
        self.assertEqual(self.exclusions([self.a, offline], {10: {1}}), set())

    def test_exclusion_is_dropped_when_the_only_other_agent_has_no_scores(self):
        self.assertEqual(self.exclusions([self.a, self.b], {10: {1}}, scores={1: SCORES}), set())

    def test_task_nobody_failed_has_no_exclusions(self):
        self.assertEqual(self.exclusions([self.a, self.b], {}), set())


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True, AGENT_HEARTBEAT_TIMEOUT=30, TASK_MAX_ATTEMPTS=3,
                   GENERATED_REPO_DIR='')
class RetryFlowTests(TestCase):
    """The whole loop through the worker API: next-task -> start -> result."""

    def setUp(self):
        self.model = AIModel.objects.create(ollama_name='test:version1', max_context=8192)
        self.a = self.make_agent('A', BETTER)  # A scores higher, so A is asked first
        self.b = self.make_agent('B', SCORES)
        self.task = Task.objects.create(title='Add', instructions='Write add(a, b).')
        self.client = Client(enforce_csrf_checks=True)

    def make_agent(self, name, scores):
        agent = Agent.objects.create(name=name, status='IDLE', last_heartbeat=timezone.now(),
                                     context_window=8192, ai_model=self.model)
        save_scores(agent, scores)
        tokens = override_settings(FR16_WORKER_TOKENS={**self.tokens(), str(agent.pk): f'token-{agent.pk}'})
        tokens.enable()
        self.addCleanup(tokens.disable)
        return agent

    @staticmethod
    def tokens():
        from django.conf import settings
        return dict(settings.FR16_WORKER_TOKENS)

    def auth(self, agent):
        return {'HTTP_AUTHORIZATION': f'Bearer token-{agent.pk}'}

    def ask(self, agent):
        return self.client.get(f'/api/agents/{agent.pk}/next-task/', **self.auth(agent))

    def post(self, agent, suffix, data, task=None):
        return self.client.post(f'/api/agents/{agent.pk}/tasks/{(task or self.task).pk}/{suffix}/',
                                json.dumps(data), content_type='application/json', **self.auth(agent))

    def start(self, agent):
        response = self.post(agent, 'start', {})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['data']

    def body(self, job, status='FAILED', error='Model unavailable', output=''):
        return dict(lease=job['lease'], attempt=job['attempt'], status=status, output=output, error=error,
                    duration_s=2, prompt_tokens=20, output_tokens=12, model_metadata={'digest': 'fixture'})

    def claim(self, agent):
        """Ask for work as `agent`, expect this test's task, and start it."""
        response = self.ask(agent)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['data']['id'], str(self.task.pk))
        return self.start(agent)

    def fail(self, agent, **changes):
        job = self.claim(agent)
        response = self.post(agent, 'result', self.body(job, **changes))
        self.assertEqual(response.status_code, 200, response.content)
        self.task.refresh_from_db()
        return job

    def go_offline(self, agent):
        Agent.objects.filter(pk=agent.pk).update(last_heartbeat=timezone.now() - timedelta(seconds=120))

    # --- rule 1: a failed attempt goes back in the queue ---

    def test_failed_attempt_puts_the_task_back_in_the_queue(self):
        self.fail(self.a)
        self.assertEqual((self.task.status, self.task.assigned_agent, self.task.attempts), ('PENDING', None, 1))
        self.a.refresh_from_db()
        self.assertEqual((self.a.status, self.a.current_task), ('IDLE', None))
        self.assertFalse(Metric.objects.get().success)

    def test_task_stays_failed_after_the_last_attempt(self):
        with override_settings(TASK_MAX_ATTEMPTS=2):
            self.fail(self.a)
            self.fail(self.b)
            self.assertEqual((self.task.status, self.task.attempts), ('FAILED', 2))
            self.assertEqual(self.ask(self.a).status_code, 204)
            self.assertEqual(self.ask(self.b).status_code, 204)

    def test_limit_of_one_means_no_retry(self):
        with override_settings(TASK_MAX_ATTEMPTS=1):
            self.fail(self.a)
        self.assertEqual(self.task.status, 'FAILED')

    def test_invalid_answer_is_retried_like_any_other_failure(self):
        """FR-18: a project task whose answer is not the agreed JSON fails the attempt."""
        self.task.project = Project.objects.create(name='Bookstore')
        self.task.type = 'BACKEND'
        self.task.save()
        self.fail(self.a, status='COMPLETED', error='', output='not json')
        self.assertEqual(self.task.status, 'PENDING')

    def test_timed_out_attempt_is_retried(self):
        self.claim(self.a)
        WorkerTaskResult.objects.update(deadline=timezone.now() - timedelta(seconds=1))
        self.ask(self.a)  # any request from the worker checks its deadlines
        self.task.refresh_from_db()
        self.assertEqual(WorkerTaskResult.objects.get(attempt=1).status, 'FAILED')
        self.assertIn(self.task.status, ('PENDING', 'ASSIGNED'))  # re-queued, and possibly claimed again
        self.assertEqual(Metric.objects.filter(success=False).count(), 1)

    def test_successful_retry_completes_the_task(self):
        self.fail(self.a)
        job = self.claim(self.b)
        self.post(self.b, 'result', self.body(job, status='COMPLETED', error='', output='def add(a, b): return a + b'))
        self.task.refresh_from_db()
        self.assertEqual((self.task.status, self.task.attempts), ('COMPLETED', 2))

    # --- rule 2: the retry is reassigned ---

    def test_retry_goes_to_another_agent(self):
        self.fail(self.a)
        self.assertEqual(self.ask(self.a).status_code, 204)  # A failed it, and B can take it
        job = self.claim(self.b)
        self.assertEqual(job['attempt'], 2)
        self.task.refresh_from_db()
        self.assertEqual(self.task.assigned_agent, self.b)

    def test_same_agent_retries_when_nobody_else_can(self):
        self.go_offline(self.b)
        self.fail(self.a)
        job = self.claim(self.a)
        self.assertEqual(job['attempt'], 2)

    def test_retry_is_told_why_the_last_attempt_failed_and_uses_a_new_seed(self):
        self.go_offline(self.b)
        first = self.fail(self.a, error='Answer was not valid JSON.')
        second = self.claim(self.a)
        self.assertNotIn('previous attempt', first['prompt'])
        self.assertIn('A previous attempt at this task failed. Reason: Answer was not valid JSON.', second['prompt'])
        self.assertEqual((first['options']['seed'], second['options']['seed']), (42, 43))

    # --- rule 3: tasks of offline workers are taken back ---

    def test_task_claimed_by_a_worker_that_went_offline_is_reassigned(self):
        self.assertEqual(self.ask(self.a).status_code, 200)  # A claims it, then disappears before starting
        self.go_offline(self.a)
        job = self.claim(self.b)
        self.assertEqual(job['attempt'], 2)
        self.assertFalse(Metric.objects.exists())  # nothing ran, so nothing counts against A

    def test_running_task_of_an_offline_worker_fails_after_its_deadline_and_is_reassigned(self):
        self.claim(self.a)
        self.go_offline(self.a)
        self.assertEqual(self.ask(self.b).status_code, 204)  # still inside the deadline: left alone
        WorkerTaskResult.objects.update(deadline=timezone.now() - timedelta(seconds=1))
        job = self.claim(self.b)
        self.assertEqual(job['attempt'], 2)
        lost = WorkerTaskResult.objects.get(attempt=1)
        self.assertEqual((lost.status, lost.error), ('FAILED', OFFLINE_ERROR))
        self.assertEqual(Metric.objects.filter(agent=self.a, success=False).count(), 1)
        self.a.refresh_from_db()
        self.assertIsNone(self.a.current_task)

    def test_online_worker_keeps_its_task(self):
        self.assertEqual(self.ask(self.a).status_code, 200)
        self.assertEqual(release_abandoned_tasks(timezone.now()), 0)
        self.assertEqual(self.ask(self.b).status_code, 204)
        self.task.refresh_from_db()
        self.assertEqual((self.task.status, self.task.assigned_agent), ('ASSIGNED', self.a))

    def test_abandoned_task_out_of_attempts_becomes_failed(self):
        with override_settings(TASK_MAX_ATTEMPTS=1):
            self.assertEqual(self.ask(self.a).status_code, 200)
            self.go_offline(self.a)
            self.assertEqual(self.ask(self.b).status_code, 204)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'FAILED')

    # --- late and repeated deliveries ---

    def test_same_failed_result_sent_again_gets_the_same_answer(self):
        job = self.claim(self.a)
        body = self.body(job)
        answers = [self.post(self.a, 'result', body) for _ in range(2)]
        self.assertEqual([answer.status_code for answer in answers], [200, 200])
        self.assertEqual(answers[1].json()['data']['status'], 'FAILED')
        self.assertEqual(Metric.objects.count(), 1)
        self.task.refresh_from_db()
        self.assertEqual((self.task.status, self.task.attempts), ('PENDING', 1))

    def test_late_result_from_the_previous_worker_is_rejected(self):
        old = self.claim(self.a)
        self.go_offline(self.a)
        WorkerTaskResult.objects.update(deadline=timezone.now() - timedelta(seconds=1))
        self.claim(self.b)
        Agent.objects.filter(pk=self.a.pk).update(last_heartbeat=timezone.now())  # A comes back
        late = self.post(self.a, 'result', self.body(old, status='COMPLETED', error='', output='late answer'))
        self.assertEqual(late.status_code, 409)
        self.task.refresh_from_db()
        self.assertEqual((self.task.status, self.task.assigned_agent), ('IN_PROGRESS', self.b))


@override_settings(TASK_MAX_ATTEMPTS=3)
class RetryExceptionsTests(TestCase):
    def setUp(self):
        self.agent = Agent.objects.create(name='worker', last_heartbeat=timezone.now())

    def test_benchmark_tasks_are_never_retried(self):
        task = Task.objects.create(title='Benchmark case', status='FAILED', attempts=1, assigned_agent=self.agent)
        run = BenchmarkRun.objects.create(agent=self.agent, model_name='test:version1')
        BenchmarkCase.objects.create(run=run, task=task, metric='coding', case_id='add')
        self.assertFalse(retry_failed_task(task, 1))
        task.refresh_from_db()
        self.assertEqual(task.status, 'FAILED')

    def test_retry_only_applies_to_the_attempt_that_failed(self):
        task = Task.objects.create(title='Moved on', status='FAILED', attempts=2)
        self.assertFalse(retry_failed_task(task, 1))  # a late call about attempt 1
        self.assertTrue(retry_failed_task(task, 2))

    def test_resolver_conflict_fails_only_when_the_resolver_is_out_of_attempts(self):
        """FR-24: a conflict stays open while its resolver task can still be retried."""
        project = Project.objects.create(name='Bookstore')
        resolver = Task.objects.create(title='Resolve', project=project, type='INTEGRATION', status='FAILED', attempts=1)
        conflict = TaskOutputConflict.objects.create(project=project, scope_key=f'project:{project.pk}',
                                                     path='backend/app.py', path_key='backend/app.py',
                                                     resolver_task=resolver, status='RESOLVING')
        self.assertTrue(after_failure(resolver, 1))
        conflict.refresh_from_db()
        self.assertEqual(conflict.status, 'RESOLVING')

        Task.objects.filter(pk=resolver.pk).update(status='FAILED', attempts=3)
        resolver.refresh_from_db()
        self.assertFalse(after_failure(resolver, 3))
        conflict.refresh_from_db()
        self.assertEqual(conflict.status, 'FAILED')
