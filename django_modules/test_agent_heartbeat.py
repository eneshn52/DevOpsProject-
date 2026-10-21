import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import APIConnection, Agent, Project, Task, Workspace


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True, AGENT_HEARTBEAT_INTERVAL=10, AGENT_HEARTBEAT_TIMEOUT=30)
class AgentHeartbeatTests(TestCase):
    def setUp(self):
        """Create one registered specialist and a task it can report."""
        Workspace.objects.get_or_create(pk=1)
        self.api = APIConnection.objects.create(name='Worker', url='http://127.0.0.1:11434')
        self.agent = Agent.objects.create(name='Backend', role='BACKEND', status='OFFLINE', api_connection=self.api)
        self.task = Task.objects.create(project=Project.objects.create(name='Bookstore'), title='Build API')

    def beat(self, data=None, agent_id=None, client=None, path='/api/agents/{}/heartbeat/'):
        """Send a worker heartbeat through Django's middleware and URL routing."""
        return (client or self.client).post(path.format(agent_id or self.agent.pk), json.dumps(data or {}), content_type='application/json')

    def test_idle_heartbeat_marks_agent_online(self):
        """Record the heartbeat time and report the expected cadence to the worker."""
        response = self.beat({'alive': True, 'status': 'IDLE'})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()['data']
        self.assertEqual((data['status'], data['currentTaskId']), ('IDLE', None))
        self.assertEqual((data['heartbeatInterval'], data['heartbeatTimeout']), (10, 30))
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 'IDLE')
        self.assertLess(timezone.now() - self.agent.last_heartbeat, timedelta(seconds=5))

    def test_busy_heartbeat_records_current_task(self):
        """Store the task a busy worker reports, then clear it when the worker is idle."""
        data = self.beat({'status': 'busy', 'currentTaskId': self.task.pk}).json()['data']
        self.assertEqual((data['status'], data['currentTaskId']), ('BUSY', str(self.task.pk)))
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.current_task, self.task)
        self.beat({'status': 'IDLE'})
        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.current_task)

    def test_path_without_trailing_slash_is_accepted(self):
        """Accept the documented POST /api/agents/{id}/heartbeat path."""
        self.assertEqual(self.beat({'status': 'IDLE'}, path='/api/agents/{}/heartbeat').status_code, 200)

    def test_alive_false_marks_agent_offline(self):
        """Let a worker announce a graceful shutdown."""
        self.beat({'status': 'BUSY', 'currentTaskId': self.task.pk})
        data = self.beat({'alive': False}).json()['data']
        self.assertEqual((data['status'], data['currentTaskId']), ('OFFLINE', None))

    def test_invalid_heartbeats_are_rejected(self):
        """Reject unknown fields, statuses, task references, and inconsistent task reports."""
        for data in [{'status': 'OFFLINE'}, {'status': 'SLEEPING'}, {'status': 7}, {'alive': 'yes'}, {'extra': 1},
                     {'status': 'BUSY'}, {'status': 'IDLE', 'currentTaskId': self.task.pk},
                     {'status': 'BUSY', 'currentTaskId': 'abc'}, {'status': 'BUSY', 'currentTaskId': True}]:
            with self.subTest(data=data):
                self.assertEqual(self.beat(data).status_code, 400)
        response = self.beat({'status': 'BUSY', 'currentTaskId': self.task.pk + 1000})
        self.assertEqual(response.json()['code'], 'TASK_NOT_FOUND')
        self.agent.refresh_from_db()
        self.assertEqual((self.agent.status, self.agent.last_heartbeat), ('OFFLINE', None))

    def test_unknown_agent_and_master_are_not_found(self):
        """Accept heartbeats only from registered specialists."""
        master = Agent.objects.create(name='Master', role='MASTER')
        self.assertEqual(self.beat(agent_id=self.agent.pk + 1000).status_code, 404)
        self.assertEqual(self.beat(agent_id=master.pk).status_code, 404)

    def test_only_post_is_allowed(self):
        """Reject reads and other methods on the heartbeat endpoint."""
        self.assertEqual(self.client.get(f'/api/agents/{self.agent.pk}/heartbeat/').status_code, 405)

    def test_worker_does_not_need_a_csrf_token(self):
        """Allow non-browser workers to post heartbeats without a CSRF cookie."""
        self.assertEqual(self.beat({'status': 'IDLE'}, client=Client(enforce_csrf_checks=True)).status_code, 200)

    @override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=False)
    def test_workspace_access_rules_apply(self):
        """Keep the existing staff-or-local access boundary for heartbeats."""
        self.assertEqual(self.beat({'status': 'IDLE'}).status_code, 403)

    def test_agent_silent_for_timeout_is_marked_offline(self):
        """Mark a busy agent offline once 30 seconds pass without a heartbeat."""
        self.beat({'status': 'BUSY', 'currentTaskId': self.task.pk})
        last = Agent.objects.get(pk=self.agent.pk).last_heartbeat
        self.assertEqual(Agent.mark_stale(now=last + timedelta(seconds=29)), 0)
        self.assertEqual(Agent.mark_stale(now=last + timedelta(seconds=31)), 1)
        self.agent.refresh_from_db()
        self.assertEqual((self.agent.status, self.agent.current_task), ('OFFLINE', None))

    def test_agents_without_heartbeat_are_offline_and_master_is_untouched(self):
        """Treat specialists that never reported as offline without changing the master."""
        silent = Agent.objects.create(name='Frontend', role='FRONTEND', status='IDLE', api_connection=self.api)
        master = Agent.objects.create(name='Master', role='MASTER', status='IDLE')
        Agent.mark_stale()
        self.assertEqual(Agent.objects.get(pk=silent.pk).status, 'OFFLINE')
        self.assertEqual(Agent.objects.get(pk=master.pk).status, 'IDLE')

    def test_agent_list_and_workspace_report_stale_agents_offline(self):
        """Apply the freshness rule whenever the browser reads agent state."""
        self.beat({'status': 'IDLE'})
        self.assertEqual(self.client.get('/api/agents/').json()['data'][0]['status'], 'IDLE')
        Agent.objects.filter(pk=self.agent.pk).update(last_heartbeat=timezone.now() - timedelta(seconds=31))
        self.assertEqual(self.client.get('/api/agents/').json()['data'][0]['status'], 'OFFLINE')
        self.beat({'status': 'IDLE'})
        Agent.objects.filter(pk=self.agent.pk).update(last_heartbeat=timezone.now() - timedelta(seconds=31))
        self.assertEqual(self.client.get('/api/workspace/').json()['data']['models'][0]['status'], 'OFFLINE')

    def test_heartbeat_after_offline_brings_agent_back(self):
        """Restore availability when a worker reconnects."""
        Agent.objects.filter(pk=self.agent.pk).update(status='OFFLINE')
        self.assertEqual(self.beat({'status': 'IDLE'}).json()['data']['status'], 'IDLE')

    def test_deleting_reported_task_clears_current_task(self):
        """Keep the agent when the task it reported is removed."""
        self.beat({'status': 'BUSY', 'currentTaskId': self.task.pk})
        self.task.delete()
        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.current_task)

    def test_management_command_marks_stale_agents(self):
        """Run the freshness sweep from a scheduled command."""
        Agent.objects.filter(pk=self.agent.pk).update(status='IDLE', last_heartbeat=timezone.now() - timedelta(seconds=31))
        output = io.StringIO()
        call_command('mark_offline_agents', stdout=output)
        self.assertIn('Marked 1 agent(s) offline.', output.getvalue())
        self.assertEqual(Agent.objects.get(pk=self.agent.pk).status, 'OFFLINE')

    def test_management_command_watch_repeats(self):
        """Repeat the sweep at the heartbeat interval in watch mode."""
        with patch('django_modules.management.commands.mark_offline_agents.time.sleep', side_effect=[None, KeyboardInterrupt]) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                call_command('mark_offline_agents', '--watch', stdout=io.StringIO())
        sleep.assert_called_with(10)

    def test_agents_api_reports_online_status_and_last_seen(self):
        """Expose the server's online verdict, heartbeat time, and seconds since it for the UI."""
        item = self.client.get('/api/agents/').json()['data'][0]
        self.assertEqual((item['status'], item['online'], item['lastHeartbeat'], item['lastSeenSeconds']), ('OFFLINE', False, None, None))
        self.beat({'status': 'BUSY', 'currentTaskId': self.task.pk})
        item = self.client.get('/api/agents/').json()['data'][0]
        self.assertEqual((item['status'], item['online']), ('BUSY', True))
        self.assertIsNotNone(item['lastHeartbeat'])
        self.assertLess(item['lastSeenSeconds'], 5)

    def test_agents_api_reports_offline_after_timeout(self):
        """Report an agent offline once its last heartbeat is more than 30 seconds old."""
        self.beat({'status': 'IDLE'})
        Agent.objects.filter(pk=self.agent.pk).update(last_heartbeat=timezone.now() - timedelta(seconds=45))
        for item in (self.client.get('/api/agents/').json()['data'][0], self.client.get('/api/workspace/').json()['data']['models'][0]):
            with self.subTest(item=item):
                self.assertEqual((item['status'], item['online']), ('OFFLINE', False))
                self.assertGreaterEqual(item['lastSeenSeconds'], 45)

    def test_online_boundary_uses_heartbeat_timeout(self):
        """Count heartbeats within the timeout as online and older ones as offline."""
        now = timezone.now()
        agent = Agent(name='Probe', role='TESTING', status='IDLE', last_heartbeat=now - timedelta(seconds=29))
        self.assertTrue(agent.is_online(now))
        self.assertFalse(agent.is_online(now + timedelta(seconds=1)))
        self.assertEqual(agent.seconds_since_heartbeat(now), 29)
        self.assertFalse(Agent(name='Never', role='TESTING', status='IDLE').is_online(now))
        self.assertFalse(Agent(name='Stopped', role='TESTING', status='OFFLINE', last_heartbeat=now).is_online(now))

    def test_online_is_computed_at_read_time_without_the_sweep(self):
        """Report a silent agent offline even when the stored status was never swept."""
        self.beat({'status': 'IDLE'})
        Agent.objects.filter(pk=self.agent.pk).update(last_heartbeat=timezone.now() - timedelta(seconds=31))
        with patch.object(Agent, 'mark_stale', return_value=0):
            item = self.client.get('/api/agents/').json()['data'][0]
        self.assertEqual((item['status'], item['online']), ('IDLE', False))

    def test_master_is_never_reported_online(self):
        """Keep the master without heartbeats from appearing as a verified online worker."""
        self.client.put('/api/master/', json.dumps({'name': 'Master', 'modelName': 'm:3b', 'apiId': str(self.api.pk)}), content_type='application/json')
        master = self.client.get('/api/master/').json()['data']
        self.assertEqual((master['online'], master['lastSeenSeconds']), (False, None))
