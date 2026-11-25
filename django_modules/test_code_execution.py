"""FR-21 integration with the real Django HTTP service and isolated interpreter."""
import os
from unittest import TestCase, skipUnless
from unittest.mock import patch

from django.test import LiveServerTestCase, override_settings

from worker import worker, code_tests
from .capabilities import save_scores
from .models import Agent, Task

CASES = [{'input': 2, 'expected': 4}, {'input': -3, 'expected': -6}]


class SandboxTests(TestCase):
    def test_case_outcomes(self):
        for source, status in [
            ('def solve(x): return x*2', 'passed'),
            ('def solve(x): return x', 'failed'),
            ('def solve(:', 'invalid_code'),
            ('def solve(x): return 1/0', 'error'),
            ('def solve(x):\n while True: pass', 'timeout'),
            ('def solve(x): return open("manage.py").read()', 'error'),
        ]:
            with self.subTest(status=status, source=source):
                report = code_tests.test_code(source, CASES)
                self.assertEqual(report['status'], status, report)
                self.assertEqual(report['exitCode'], int(status != 'passed'))
                self.assertEqual(len(report['cases']), 2)
                self.assertGreater(report['duration'], 0)

    def test_streams(self):
        source = 'import sys\ndef solve(x):\n print("answer")\n print("trace", file=sys.stderr)\n return x*2'
        case = code_tests.test_code(source, CASES)['cases'][0]
        self.assertEqual((case['stdout'], case['stderr']), ('answer\n', 'trace\n'))
        self.assertEqual(case['status'], 'passed')

    def test_no_tests(self):
        r = code_tests.test_code('def solve(x): return x', [])
        self.assertEqual((r['status'], r['cases'], r['exitCode']), ('missing_tests', [], 1))


@override_settings(WORKSPACE_ALLOW_LOCAL_ACCESS=True)
class ExecutionFlowTests(LiveServerTestCase):
    host = '127.0.0.1'

    def setUp(self):
        self.agent = Agent.objects.create(name='local-python', context_window=8192)
        save_scores(self.agent, dict(coding=80, reasoning=80, context=80, speed=80))
        self.task = Task.objects.create(title='Double an integer', instructions='Return twice the input integer.',
                                        expected_output='Return one integer directly from solve(value).',
                                        test_cases=CASES, max_tokens=512)
        self.config = dict(worker.DEFAULTS, agent_id=self.agent.pk, server_url=self.live_server_url,
                           run_tests=True, model=os.getenv('FR21_MODEL', 'fixture'))

    def verify_flow(self):
        self.assertEqual(worker.run(self.config, once=True), 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'COMPLETED', self.task.test_result)
        path = f'/api/agents/{self.agent.pk}/tasks/{self.task.pk}/test-result/'
        self.assertEqual(self.client.get(path).json()['data'], self.task.test_result)
        self.assertEqual(len(self.task.test_result['cases']), len(CASES))

    def test_generated_fixture(self):
        with patch.object(worker, 'ollama_problem', return_value=None), patch.object(
                code_tests, 'generate', return_value='def solve(value): return value * 2'):
            self.verify_flow()

    @skipUnless(os.getenv('FR21_MODEL'), 'Set FR21_MODEL to run against a local Ollama model.')
    def test_local_model(self):
        self.config['ollama_url'] = os.getenv('FR21_OLLAMA_URL', self.config['ollama_url'])
        self.verify_flow()
