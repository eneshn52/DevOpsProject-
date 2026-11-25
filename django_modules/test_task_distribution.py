"""Tests for task distribution: GET /api/agents/{id}/next-task."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from . import task_distribution
from .capabilities import save_scores
from .models import Agent, Metric, Project, Task, TaskDependency, TaskPlan

# The brief's agents A, B and C (speed values chosen by the team)
AGENT_A = {'coding': 90, 'reasoning': 85, 'context': 95, 'speed': 50}
AGENT_B = {'coding': 75, 'reasoning': 95, 'context': 70, 'speed': 50}
AGENT_C = {'coding': 95, 'reasoning': 70, 'context': 80, 'speed': 60}


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True, AGENT_HEARTBEAT_TIMEOUT=30)
class NextTaskTests(TestCase):
    def setUp(self):
        """Agents A, B and C online with scores, and a project to add tasks to."""
        self.project = Project.objects.create(name='Bookstore')
        self.a, self.b, self.c = (self.make_agent(name, scores)
                                  for name, scores in [('A', AGENT_A), ('B', AGENT_B), ('C', AGENT_C)])

    def make_agent(self, name, scores=None, **fields):
        agent = Agent.objects.create(name=name, status='IDLE', last_heartbeat=timezone.now(),
                                     context_window=8192, **fields)
        if scores:
            save_scores(agent, scores)
        return agent

    def make_task(self, task_type, **fields):
        return Task.objects.create(project=self.project, type=task_type, title=f'{task_type} task', **fields)

    def ask(self, agent, path='/api/agents/{}/next-task/'):
        return self.client.get(path.format(agent.pk))

    # --- choosing the right task ---

    def test_best_agent_gets_the_task_and_it_is_claimed(self):
        backend = self.make_task('BACKEND', instructions='Build the API')
        response = self.ask(self.a)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()['data']
        self.assertEqual((data['id'], data['type'], data['instructions'], data['attempt']),
                         (str(backend.pk), 'BACKEND', 'Build the API', 1))
        backend.refresh_from_db()
        self.assertEqual((backend.status, backend.assigned_agent, backend.attempts), ('ASSIGNED', self.a, 1))

    def test_agent_gets_nothing_when_a_better_agent_should_take_the_task(self):
        backend = self.make_task('BACKEND')
        response = self.ask(self.c)  # A wins backend 85.5 vs 85.0
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b'')
        backend.refresh_from_db()
        self.assertEqual(backend.status, 'PENDING')

    def test_assignment_uses_and_returns_the_tasks_stored_weights(self):
        """A persisted reasoning priority selects B, overriding the BACKEND type fallback."""
        backend = self.make_task('BACKEND')
        weights = {'coding': 0, 'reasoning': 1, 'context': 0, 'speed': 0}
        Task.objects.filter(pk=backend.pk).update(capability_weights=weights)
        self.assertEqual(self.ask(self.a).status_code, 204)
        response = self.ask(self.b)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['data']['id'], str(backend.pk))
        self.assertEqual(response.json()['data']['capabilityWeights'], weights)

    def test_agent_takes_the_task_it_is_best_at(self):
        self.make_task('BACKEND')
        frontend = self.make_task('FRONTEND')
        self.assertEqual(self.ask(self.c).json()['data']['id'], str(frontend.pk))  # C wins frontend 84 vs 82

    def test_task_failed_by_the_best_agent_goes_to_the_next_best(self):
        backend = self.make_task('BACKEND')
        Metric.objects.create(task=backend, agent=self.a, success=False)
        self.assertEqual(self.ask(self.c).json()['data']['id'], str(backend.pk))

    def test_busy_agent_counts_as_queued(self):
        self.make_task('FRONTEND', status='ASSIGNED', assigned_agent=self.a)
        backend = self.make_task('BACKEND')
        self.assertEqual(self.ask(self.c).json()['data']['id'], str(backend.pk))  # A drops to 75.5

    # --- which tasks are ready ---

    def test_task_waits_for_its_dependencies(self):
        backend = self.make_task('BACKEND')
        frontend = self.make_task('FRONTEND')
        TaskDependency.objects.create(task=frontend, depends_on=backend)
        self.assertEqual(self.ask(self.c).status_code, 204)  # frontend blocked, backend is A's
        Task.objects.filter(pk=backend.pk).update(status='COMPLETED')
        self.assertEqual(self.ask(self.c).json()['data']['id'], str(frontend.pk))

    def test_claimed_and_finished_tasks_are_not_handed_out(self):
        self.make_task('BACKEND', status='ASSIGNED', assigned_agent=self.b)
        self.make_task('BACKEND', status='COMPLETED')
        self.assertEqual(self.ask(self.a).status_code, 204)

    def test_tasks_of_archived_projects_and_unsaved_plans_are_skipped(self):
        Project.objects.filter(pk=self.project.pk).update(archived_at=timezone.now())
        self.make_task('BACKEND')
        other = Project.objects.create(name='Shop')
        plan = TaskPlan.objects.create(project=other, request_text='x', model_name='m', agent_name='Master')
        Task.objects.create(project=other, plan=plan, plan_key='t1', type='BACKEND', title='Draft')
        self.assertEqual(self.ask(self.a).status_code, 204)

    def test_unknown_task_type_uses_equal_weights(self):
        general = self.make_task('GENERAL')
        self.assertEqual(self.ask(self.a).json()['data']['id'], str(general.pk))  # A 80, C 76.25, B 72.5

    # --- the asking agent ---

    def test_agent_holding_a_task_gets_the_same_task_back(self):
        backend = self.make_task('BACKEND')
        first = self.ask(self.a).json()['data']
        self.make_task('BACKEND')
        second = self.ask(self.a).json()['data']
        self.assertEqual((second['id'], second['attempt']), (str(backend.pk), first['attempt']))

    def test_offline_agent_must_send_a_heartbeat_first(self):
        self.make_task('BACKEND')
        Agent.objects.filter(pk=self.a.pk).update(last_heartbeat=timezone.now() - timedelta(seconds=60))
        response = self.ask(self.a)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'AGENT_OFFLINE'))

    def test_agent_without_scores_gets_nothing(self):
        new = self.make_agent('New')
        self.make_task('BACKEND')
        self.assertEqual(self.ask(new).status_code, 204)

    def test_unknown_agent_and_master_are_not_found(self):
        master = Agent.objects.create(name='Master', role='MASTER', last_heartbeat=timezone.now())
        self.assertEqual(self.client.get('/api/agents/9999/next-task/').status_code, 404)
        self.assertEqual(self.ask(master).status_code, 404)

    # --- the claim ---

    def test_task_claimed_by_someone_else_meanwhile_is_not_given_twice(self):
        backend = self.make_task('BACKEND')
        real_choose = task_distribution.choose_task
        calls = []

        def another_worker_wins_first(agent, now):
            """The first choice is taken by B just before A's claim, like two workers asking at once."""
            choice = real_choose(agent, now)
            if not calls:
                Task.objects.filter(pk=choice.pk).update(status='ASSIGNED', assigned_agent=self.b)
            calls.append(choice)
            return choice

        with patch.object(task_distribution, 'choose_task', side_effect=another_worker_wins_first):
            response = self.ask(self.a)
        self.assertEqual(response.status_code, 204)
        backend.refresh_from_db()
        self.assertEqual((backend.assigned_agent, backend.attempts), (self.b, 0))

    # --- the HTTP boundary ---

    def test_path_without_trailing_slash_works(self):
        self.make_task('BACKEND')
        self.assertEqual(self.ask(self.a, path='/api/agents/{}/next-task').status_code, 200)

    def test_only_get_is_allowed(self):
        self.assertEqual(self.client.post(f'/api/agents/{self.a.pk}/next-task/').status_code, 405)

    @override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=False)
    def test_workspace_access_rules_apply(self):
        self.assertEqual(self.ask(self.a).status_code, 403)
