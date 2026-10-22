"""Run the standalone heartbeat worker against a live server."""
import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase, SimpleTestCase, override_settings

from worker import worker

from .models import APIConnection, AIModel, Agent, Workspace


def worker_config(**overrides):
    """Build a worker configuration without reading files or environment secrets."""
    return {**worker.DEFAULTS, 'interval': 10, **overrides}


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class WorkerLiveServerTests(LiveServerTestCase):
    host = '127.0.0.1'  # IPv4 avoids slow localhost resolution on Windows

    def setUp(self):
        """Register the specialist the worker identifies as."""
        Workspace.objects.get_or_create(pk=1)
        api = APIConnection.objects.create(name='Local Ollama', url='http://127.0.0.1:11434')
        model = AIModel.objects.create(ollama_name='qwen2.5-coder:3b')
        self.agent = Agent.objects.create(name='Qwen', role='GENERAL', status='OFFLINE', api_connection=api, ai_model=model)

    def run_once(self, **overrides):
        """Send one heartbeat with Ollama reported ready for the specialist's model."""
        with patch.object(worker, 'ollama_problem', return_value=None) as probe:
            code = worker.run(worker_config(server_url=self.live_server_url, **overrides), once=True)
        return code, probe

    def test_worker_finds_agent_by_name_and_brings_it_online(self):
        """Resolve the specialist by case-insensitive name and record its heartbeat."""
        code, probe = self.run_once(agent_name='qwen')
        self.assertEqual(code, 0)
        self.assertEqual(probe.call_args.args[1], 'qwen2.5-coder:3b')
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.is_online())

    def test_worker_finds_agent_by_id(self):
        """Accept a numeric specialist id instead of a name."""
        self.assertEqual(self.run_once(agent_id=self.agent.pk)[0], 0)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 'IDLE')

    def test_unreachable_ollama_sends_no_heartbeat(self):
        """Leave the agent offline when its local model endpoint is not ready."""
        with patch.object(worker, 'ollama_problem', return_value='Ollama is not reachable'):
            code = worker.run(worker_config(server_url=self.live_server_url, agent_name='Qwen'), once=True)
        self.assertEqual(code, 1)
        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.last_heartbeat)

    def test_unknown_agent_is_reported(self):
        """List registered specialists when the configured name does not match."""
        with self.assertRaisesRegex(worker.WorkerError, r'Qwen \(id \d+\)'):
            self.run_once(agent_name='Missing')

    @override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=False)
    def test_remote_worker_signs_in_as_staff(self):
        """Use a staff session when loopback access is disabled, as on another computer."""
        get_user_model().objects.create_user('worker', password='worker-pass-123', is_staff=True)
        self.assertEqual(worker.main(['--server-url', self.live_server_url, '--agent-name', 'Qwen', '--once']), 2)
        self.assertEqual(self.run_once(agent_name='Qwen', username='worker', password='worker-pass-123')[0], 0)
        with self.assertRaisesRegex(worker.WorkerError, 'Sign-in failed'):
            self.run_once(agent_name='Qwen', username='worker', password='wrong')


class WorkerUnitTests(SimpleTestCase):
    def test_ollama_check_requires_the_model(self):
        """Report a missing model and accept names without an explicit tag as :latest."""
        tags = json.dumps({'models': [{'name': 'qwen2.5-coder:3b'}, {'name': 'llama3:latest'}]}).encode()
        with patch.object(worker.urllib.request, 'urlopen', side_effect=lambda *a, **k: io.BytesIO(tags)):
            self.assertIsNone(worker.ollama_problem('http://localhost:11434', 'qwen2.5-coder:3b', '', 1))
            self.assertIsNone(worker.ollama_problem('http://localhost:11434', 'llama3', '', 1))
            self.assertIn('ollama pull', worker.ollama_problem('http://localhost:11434', 'mistral:7b', '', 1))
        with patch.object(worker.urllib.request, 'urlopen', side_effect=OSError('refused')):
            self.assertIn('not reachable', worker.ollama_problem('http://localhost:11434', 'x', '', 1))

    def test_config_validation(self):
        """Require an agent and keep the interval below the 30 second timeout."""
        args = worker.argparse.Namespace(config=None, server_url=None, agent_id=None, agent_name=None, ollama_url=None, model=None, interval=None)
        with self.assertRaisesRegex(worker.WorkerError, 'agent_id or agent_name'):
            worker.load_config(args)
        args.agent_name, args.interval = 'Qwen', 30
        with self.assertRaisesRegex(worker.WorkerError, 'interval'):
            worker.load_config(args)
        args.interval = 10
        self.assertEqual(worker.load_config(args)['agent_name'], 'Qwen')

    def test_ctrl_c_stops_without_signing_off_by_default(self):
        """Let the agent time out after Ctrl+C unless --sign-off is requested."""
        client = patch.object(worker, 'Workspace').start(); self.addCleanup(patch.stopall)
        client.return_value.find_agent.return_value = {'id': '7', 'name': 'Qwen', 'modelName': 'm'}
        patch.object(worker, 'ollama_problem', return_value=None).start()
        patch.object(worker, 'wait', side_effect=KeyboardInterrupt).start()
        self.assertEqual(worker.run(worker_config(agent_name='Qwen')), 0)
        client.return_value.heartbeat.assert_called_once_with('7')
        client.return_value.heartbeat.reset_mock()
        self.assertEqual(worker.run(worker_config(agent_name='Qwen'), sign_off=True), 0)
        client.return_value.heartbeat.assert_called_with('7', alive=False)
