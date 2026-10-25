"""Runner integration checks without a real LLM or generated code execution."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmark_common import save
from run_benchmarks import parse_selection, run_test, select_model


class RunnerTests(unittest.TestCase):
    def test_selections(self):
        self.assertEqual(parse_selection(' 2,3,2 '), ['reasoning', 'context'])
        self.assertEqual(parse_selection('0'), ['coding', 'reasoning', 'context', 'speed'])
        for value in ('', '0,1', '5', '1,', 'abc'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_selection(value)

    def test_model_selection_retries(self):
        with patch('builtins.input', side_effect=['0', 'x', '2']):
            self.assertEqual(select_model(['first', 'second']), 'second')

    def run_fixture(self, action):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'model' / 'result.json'
            target.parent.mkdir()
            def fake_main():
                save(target, {'model': 'model', 'status': 'running', 'tasks': []})
                action(target)
            with patch('run_benchmarks.importlib.import_module', return_value=SimpleNamespace(main=fake_main)):
                summary, interrupted = run_test('reasoning', 'model', 'http://localhost:11434', 'quick', root)
            return summary, interrupted, json.loads(target.read_text())

    def test_completed_with_wrong_answers_is_not_execution_failure(self):
        summary, interrupted, data = self.run_fixture(lambda path: save(path, {'status': 'completed', 'tasks': [{'passed': False}]}))
        self.assertEqual(summary['status'], 'completed')
        self.assertFalse(interrupted)
        self.assertIn('updated_at', data)

    def test_request_errors_are_not_reported_as_success(self):
        summary, _, data = self.run_fixture(lambda path: save(path, {'status': 'completed', 'tasks': [{'error': 'timeout'}]}))
        self.assertEqual(summary['status'], 'error')
        self.assertEqual(data['status'], 'error')

    def test_generated_code_failure_counts_as_model_result(self):
        summary, _, data = self.run_fixture(lambda path: save(path, {
            'status': 'completed', 'score_valid': True,
            'tasks': [{'error': 'function not found', 'error_kind': 'model_output'}],
        }))
        self.assertEqual(summary['status'], 'completed')
        self.assertTrue(data['score_valid'])

    def test_interruption_preserves_result(self):
        def interrupt(path):
            raise KeyboardInterrupt
        summary, interrupted, data = self.run_fixture(interrupt)
        self.assertTrue(interrupted)
        self.assertEqual(data['status'], 'interrupted')

    def test_unexpected_failure_preserves_result(self):
        def fail(path):
            raise ValueError('Malformed API response')
        summary, interrupted, data = self.run_fixture(fail)
        self.assertFalse(interrupted)
        self.assertEqual(summary['status'], 'error')
        self.assertEqual(data['runner_error'], 'Malformed API response')


if __name__ == '__main__':
    unittest.main()
