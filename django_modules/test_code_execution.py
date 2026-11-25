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

    def verify_flow(self, expected='COMPLETED'):
        self.assertEqual(worker.run(self.config, once=True), 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, expected, self.task.test_result)
        path = f'/api/agents/{self.agent.pk}/tasks/{self.task.pk}/test-result/'
        self.assertEqual(self.client.get(path).json()['data'], self.task.test_result)
        self.assertEqual(len(self.task.test_result['cases']), len(CASES))

    def test_generated_fixture(self):
        with patch.object(worker, 'ollama_problem', return_value=None), patch.object(
                code_tests, 'generate', return_value='def solve(value): return value * 2'), patch.object(
                code_tests, 'review_code', return_value={'status': 'approved', 'comments': ['value * 2 doubles both signed inputs.'],
                    'issues': [], 'improvements': [], 'rejectionReasons': []}):
            self.verify_flow()

    def test_rejected_even_with_passing_examples(self):
        review = {'status': 'rejected', 'comments': ['The lookup table omits zero.'],
                  'issues': ['solve(0) raises KeyError'], 'improvements': ['Double the input directly.'],
                  'rejectionReasons': ['Only handles the two example inputs.']}
        with patch.object(worker, 'ollama_problem', return_value=None), patch.object(
                code_tests, 'generate', return_value='def solve(x): return {2:4,-3:-6}[x]'), patch.object(
                code_tests, 'review_code', return_value=review):
            self.verify_flow('FAILED')
        self.assertEqual(self.task.test_result['status'], 'passed')
        self.assertEqual(self.task.test_result['review'], review)

    def test_legacy_result_without_review_cannot_complete(self):
        self.task.assigned_agent = self.agent
        self.task.status = 'ASSIGNED'
        self.task.save()
        path = f'/api/agents/{self.agent.pk}/tasks/{self.task.pk}/test-result/'
        body = {'status': 'passed', 'source': 'def solve(x): return x*2', 'exitCode': 0,
                'cases': [{'status': 'passed'} for _ in CASES], 'attempt': self.task.attempts}
        invalid = self.client.post(path, dict(body, review={'status': 'approved'}), content_type='application/json')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.post(path, body, content_type='application/json').status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'FAILED')

    @skipUnless(os.getenv('FR21_MODEL'), 'Set FR21_MODEL to run against a local Ollama model.')
    def test_local_model(self):
        self.config['ollama_url'] = os.getenv('FR21_OLLAMA_URL', self.config['ollama_url'])
        self.verify_flow()
        self.assertEqual(self.task.test_result['review']['status'], 'approved')

    @skipUnless(os.getenv('FR21_MODEL'), 'Set FR21_MODEL for real review of failed tests.')
    def test_local_model_rejection(self):
        self.config['ollama_url'] = os.getenv('FR21_OLLAMA_URL', self.config['ollama_url'])
        self.task.test_cases = [{'input': 2, 'expected': 4}, {'input': 2, 'expected': 5}]
        self.task.save(update_fields=['test_cases'])
        self.verify_flow('FAILED')
        self.assertEqual(self.task.test_result['status'], 'failed')
        self.assertEqual(self.task.test_result['review']['status'], 'rejected', self.task.test_result)
