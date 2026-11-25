"""FR-10: agent profiles are updated from real task outcomes after each task."""
import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import agent_profiles
from .agent_profiles import adjusted_scores, blend, observed_weight, profile_json, refresh_profile
from .capabilities import get_scores, save_scores
from .models import (AgentCapabilityScore, AgentProfile, AIModel, Agent, BenchmarkCase, BenchmarkRun, Metric,
                     Project, Task, WorkerTaskResult)

BENCHMARK = {'coding': 90, 'reasoning': 80, 'context': 70, 'speed': 60}


def make_agent(name='worker', scores=None, **fields):
    agent = Agent.objects.create(name=name, status='IDLE', last_heartbeat=timezone.now(), context_window=8192, **fields)
    if scores:
        save_scores(agent, scores)
    return agent


def outcome(agent, success=True, duration_s=10.0, output_tokens=0, task=None):
    """Record one finished attempt the way FR-16 does: a Metric row."""
    task = task or Task.objects.create(title='real task', status='COMPLETED' if success else 'FAILED')
    return Metric.objects.create(task=task, agent=agent, model='m', success=success,
                                 duration_s=duration_s, output_tokens=output_tokens)


class RefreshProfileTests(TestCase):
    def setUp(self):
        self.agent = make_agent()

    def test_counts_success_rate_and_time_spent(self):
        outcome(self.agent, True, 10)
        outcome(self.agent, True, 20)
        outcome(self.agent, False, 30)
        profile = refresh_profile(self.agent)
        self.assertEqual((profile.tasks_completed, profile.tasks_failed, profile.sample_size), (2, 1, 3))
        self.assertAlmostEqual(profile.success_rate, 0.6667)
        self.assertEqual(profile.avg_duration_s, 20.0)
        self.assertEqual(profile.total_time_s, 60.0)

    def test_no_outcomes_gives_an_empty_profile(self):
        profile = refresh_profile(self.agent)
        self.assertEqual((profile.tasks_completed, profile.tasks_failed, profile.sample_size), (0, 0, 0))
        self.assertIsNone(profile.success_rate)
        self.assertIsNone(profile.avg_duration_s)
        self.assertIsNone(profile.tokens_per_second)

    def test_benchmark_tasks_are_not_real_work(self):
        run = BenchmarkRun.objects.create(agent=self.agent, model_name='m')
        bench_task = Task.objects.create(title='benchmark case')
        BenchmarkCase.objects.create(run=run, task=bench_task, metric='coding', case_id='add')
        outcome(self.agent, False, 99, task=bench_task)
        outcome(self.agent, True, 5)
        profile = refresh_profile(self.agent)
        self.assertEqual((profile.tasks_completed, profile.tasks_failed), (1, 0))
        self.assertEqual(profile.total_time_s, 5.0)

    def test_other_agents_outcomes_are_not_counted(self):
        other = make_agent('other')
        outcome(other, False)
        outcome(self.agent, True)
        self.assertEqual(refresh_profile(self.agent).tasks_failed, 0)

    def test_rates_follow_recent_outcomes_but_counts_cover_everything(self):
        for _ in range(3):
            outcome(self.agent, False, 100)
        for _ in range(2):
            outcome(self.agent, True, 10)
        with patch.object(agent_profiles, 'RECENT_WINDOW', 2):
            profile = refresh_profile(self.agent)
        self.assertEqual((profile.tasks_completed, profile.tasks_failed), (2, 3))
        self.assertEqual((profile.sample_size, profile.success_rate, profile.avg_duration_s), (2, 1.0, 10.0))
        self.assertEqual(profile.total_time_s, 320.0)

    def test_speed_is_the_median_of_usable_successful_samples(self):
        outcome(self.agent, True, 10, output_tokens=100)    # 10 tok/s
        outcome(self.agent, True, 10, output_tokens=300)    # 30 tok/s
        outcome(self.agent, True, 10, output_tokens=200)    # 20 tok/s -> median
        outcome(self.agent, False, 1, output_tokens=900)    # failure: ignored
        outcome(self.agent, True, 1, output_tokens=10)      # too few tokens: ignored
        outcome(self.agent, True, 0, output_tokens=500)     # no timing: ignored
        self.assertEqual(refresh_profile(self.agent).tokens_per_second, 20.0)

    def test_refresh_is_repeatable_and_keeps_one_row(self):
        outcome(self.agent, True, 4)
        first = refresh_profile(self.agent)
        second = refresh_profile(self.agent)
        self.assertEqual(AgentProfile.objects.count(), 1)
        self.assertEqual((first.pk, first.tasks_completed), (second.pk, second.tasks_completed))

    def test_last_outcome_time_comes_from_the_finished_attempt(self):
        task = Task.objects.create(title='t', assigned_agent=self.agent)
        done = timezone.now() - timedelta(hours=2)
        WorkerTaskResult.objects.create(task=task, agent=self.agent, attempt=1, model_name='m', prompt='p',
                                        context_window=8192, deadline=timezone.now(), status='COMPLETED',
                                        completed_at=done)
        outcome(self.agent, True, task=task)
        self.assertEqual(refresh_profile(self.agent).last_outcome_at, done)

    def test_profile_json(self):
        self.assertIsNone(profile_json(self.agent))
        outcome(self.agent, True, 3)
        refresh_profile(self.agent)
        self.agent.refresh_from_db()
        data = profile_json(Agent.objects.select_related('profile').get(pk=self.agent.pk))
        self.assertEqual((data['tasksCompleted'], data['tasksFailed'], data['successRate'], data['avgDurationS']),
                         (1, 0, 1.0, 3.0))


class BlendTests(TestCase):
    def profile(self, sample_size, success_rate, tokens_per_second=None):
        return AgentProfile(sample_size=sample_size, success_rate=success_rate, tokens_per_second=tokens_per_second)

    def test_weight_grows_with_outcomes_and_is_capped(self):
        self.assertEqual(observed_weight(0), 0)
        self.assertAlmostEqual(observed_weight(5), 1 / 3)
        self.assertEqual(observed_weight(10), 0.5)
        self.assertEqual(observed_weight(1000), 0.5)

    def test_without_a_track_record_the_benchmark_is_used_as_is(self):
        self.assertEqual(blend(BENCHMARK, None), BENCHMARK)
        self.assertEqual(blend(BENCHMARK, self.profile(0, None)), BENCHMARK)

    def test_success_rate_moves_coding_and_reasoning(self):
        scores = blend(BENCHMARK, self.profile(10, 0.5))  # weight 0.5, observed 50
        self.assertEqual((scores['coding'], scores['reasoning']), (70.0, 65.0))
        self.assertEqual(scores['context'], 70)  # task outcomes do not measure context
        self.assertEqual(scores['speed'], 60)    # no speed sample yet

    def test_real_speed_moves_speed(self):
        # 10 tok/s is 50 on the FR-9 scale (target 20 tok/s); weight 0.5 -> (60 + 50) / 2
        self.assertEqual(blend(BENCHMARK, self.profile(10, 1.0, tokens_per_second=10))['speed'], 55.0)

    def test_one_failure_cannot_bury_a_good_benchmark(self):
        scores = blend(BENCHMARK, self.profile(1, 0.0))  # weight 1/11
        self.assertGreater(scores['coding'], 80)

    def test_adjusted_scores_keep_unbenchmarked_agents_out_and_never_overwrite_scores(self):
        good, unscored = make_agent('good', BENCHMARK), make_agent('unscored')
        for agent in (good, unscored):
            for _ in range(10):
                outcome(agent, False)
            refresh_profile(agent)
        adjusted = adjusted_scores(get_scores([good.pk, unscored.pk]))
        self.assertEqual(list(adjusted), [good.pk])
        self.assertEqual(adjusted[good.pk]['coding'], 45.0)
        self.assertEqual(get_scores([good.pk])[good.pk], {k: float(v) for k, v in BENCHMARK.items()})
        self.assertEqual(AgentCapabilityScore.objects.filter(agent=good, metric__name='coding').get().score, 90.0)


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class ProfileUpdatesAfterEachTaskTests(TestCase):
    """End to end through the FR-16 worker endpoints."""

    def setUp(self):
        model = AIModel.objects.create(ollama_name='test:version1', max_context=8192)
        self.agent = make_agent(ai_model=model)
        tokens = override_settings(FR16_WORKER_TOKENS={str(self.agent.pk): 'test-fr10'})
        tokens.enable()
        self.addCleanup(tokens.disable)
        self.worker = Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION='Bearer test-fr10')

    def run_task(self, status='COMPLETED', duration_s=4, output_tokens=64):
        task = Task.objects.create(title='Add', instructions='Write add(a,b).', assigned_agent=self.agent,
                                   status='ASSIGNED')
        url = f'/api/agents/{self.agent.pk}/tasks/{task.pk}/'
        job = self.worker.post(url + 'start/', '{}', content_type='application/json').json()['data']
        body = {'lease': job['lease'], 'attempt': job['attempt'], 'status': status, 'duration_s': duration_s,
                'prompt_tokens': 20, 'output_tokens': output_tokens,
                'output': 'def add(a, b):\n    return a + b' if status == 'COMPLETED' else '',
                'error': '' if status == 'COMPLETED' else 'Model crashed',
                'model_metadata': {'digest': 'sha256:fixture'}}
        return self.worker.post(url + 'result/', json.dumps(body), content_type='application/json')

    def profile(self):
        return AgentProfile.objects.get(agent=self.agent)

    def test_success_updates_the_profile(self):
        self.assertEqual(self.run_task(duration_s=4, output_tokens=64).status_code, 200)
        profile = self.profile()
        self.assertEqual((profile.tasks_completed, profile.tasks_failed, profile.success_rate), (1, 0, 1.0))
        self.assertEqual((profile.avg_duration_s, profile.tokens_per_second), (4.0, 16.0))
        self.assertIsNotNone(profile.last_outcome_at)

    def test_failure_updates_the_profile(self):
        self.run_task()
        self.assertEqual(self.run_task('FAILED', duration_s=8).status_code, 200)
        profile = self.profile()
        self.assertEqual((profile.tasks_completed, profile.tasks_failed, profile.success_rate), (1, 1, 0.5))
        self.assertEqual((profile.avg_duration_s, profile.total_time_s), (6.0, 12.0))

    def test_timeout_counts_as_a_failure(self):
        task = Task.objects.create(title='Slow', instructions='x', assigned_agent=self.agent, status='ASSIGNED')
        self.worker.post(f'/api/agents/{self.agent.pk}/tasks/{task.pk}/start/', '{}', content_type='application/json')
        WorkerTaskResult.objects.update(deadline=timezone.now() - timedelta(seconds=1))
        from .task_execution import expire_executions
        expire_executions(self.agent)
        self.assertEqual((self.profile().tasks_completed, self.profile().tasks_failed), (0, 1))

    def test_workspace_api_shows_the_profile(self):
        before = self.client.get('/api/agents/').json()['data'][0]
        self.assertIsNone(before['profile'])
        self.run_task(duration_s=2)
        after = self.client.get('/api/agents/').json()['data'][0]['profile']
        self.assertEqual((after['tasksCompleted'], after['tasksFailed'], after['successRate'], after['avgDurationS']),
                         (1, 0, 1.0, 2.0))


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True, AGENT_HEARTBEAT_TIMEOUT=30)
class SchedulerUsesTrackRecordTests(TestCase):
    """FR-14 picks agents with the FR-10 adjusted scores."""

    def setUp(self):
        self.project = Project.objects.create(name='Bookstore')
        self.reliable = make_agent('reliable', BENCHMARK)
        self.flaky = make_agent('flaky', {**BENCHMARK, 'coding': 95})  # better on paper

    def test_a_reliable_agent_beats_a_flaky_one_with_better_benchmarks(self):
        for _ in range(10):
            outcome(self.flaky, False)
            outcome(self.reliable, True)
        refresh_profile(self.flaky)
        refresh_profile(self.reliable)
        task = Task.objects.create(project=self.project, type='BACKEND', title='Build the API')
        self.assertEqual(self.client.get(f'/api/agents/{self.flaky.pk}/next-task/').status_code, 204)
        response = self.client.get(f'/api/agents/{self.reliable.pk}/next-task/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['data']['id'], str(task.pk))

    def test_without_track_records_the_benchmark_decides(self):
        Task.objects.create(project=self.project, type='BACKEND', title='Build the API')
        self.assertEqual(self.client.get(f'/api/agents/{self.reliable.pk}/next-task/').status_code, 204)
        self.assertEqual(self.client.get(f'/api/agents/{self.flaky.pk}/next-task/').status_code, 200)


class RebuildCommandTests(TestCase):
    def test_rebuilds_profiles_from_existing_outcomes(self):
        agent = make_agent('old-worker')
        outcome(agent, True)
        outcome(agent, False)
        Agent.objects.create(name='Master', role='MASTER')
        out = io.StringIO()
        call_command('rebuild_agent_profiles', stdout=out)
        self.assertIn('old-worker: 1 completed, 1 failed, recent success 50%', out.getvalue())
        self.assertIn('Rebuilt 1 profile(s).', out.getvalue())
        self.assertEqual(AgentProfile.objects.count(), 1)
