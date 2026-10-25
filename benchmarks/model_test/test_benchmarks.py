"""Audit regression checks; no live models, downloads or previous result changes."""
from collections import Counter
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

import benchmark_common as common
import model_coding_test as coding
import model_context_test as context
import model_reasoning_test as reasoning
import model_speed_test as speed
from run_benchmarks import main as runner_main, run_test


REFERENCE_CODE = '''
def remove_adjacent_groups(text, k):
    stack = []
    for char in text:
        if stack and stack[-1][0] == char:
            stack[-1][1] += 1
        else:
            stack.append([char, 1])
        if stack[-1][1] == k:
            stack.pop()
    return ''.join(char * count for char, count in stack)

def apply_atomic_batches(initial, batches):
    balances = initial.copy()
    for batch in batches:
        trial = balances.copy()
        valid = True
        for op in batch:
            if (not isinstance(op, list) or len(op) != 2 or
                    not isinstance(op[0], str) or op[0] not in trial or type(op[1]) is not int):
                valid = False
                break
            trial[op[0]] += op[1]
            if trial[op[0]] < 0:
                valid = False
                break
        if valid:
            balances = trial
    return balances

def apply_patch(lines, operations):
    result = lines.copy()
    for op in operations:
        if (not isinstance(op, list) or not op or not isinstance(op[0], str) or
                op[0] not in ('insert', 'delete', 'replace')):
            raise ValueError()
        kind = op[0]
        length = 2 if kind == 'delete' else 3
        if len(op) != length or type(op[1]) is not int:
            raise ValueError()
        index = op[1]
        limit = len(result) + (1 if kind == 'insert' else 0)
        if not 0 <= index < limit or (kind != 'delete' and not isinstance(op[2], str)):
            raise ValueError()
        if kind == 'insert':
            result.insert(index, op[2])
        elif kind == 'delete':
            result.pop(index)
        else:
            result[index] = op[2]
    return result

def aggregate_logs(records):
    buckets = {}
    for r in records:
        if not isinstance(r, list) or len(r) != 4:
            continue
        timestamp, service, status, latency = r
        if (type(timestamp) is not int or not isinstance(service, str) or not service or
                type(status) is not int or type(latency) is not int or latency < 0):
            continue
        bucket = buckets.setdefault(service, [0, 0, 0])
        bucket[0] += 1
        bucket[1] += status >= 500
        bucket[2] += latency
    return {s: {'count': n, 'errors': e, 'avg_latency': total / n}
            for s, (n, e, total) in buckets.items()}
'''


class CodingAuditTests(unittest.TestCase):
    def test_counts_and_difficulty_distribution_are_preserved(self):
        self.assertEqual(Counter(t['level'] for t in coding.TASKS),
                         {'easy': 4, 'medium': 6, 'hard': 6, 'expert': 4})
        self.assertEqual(len({t['id'] for t in coding.TASKS}), 20)
        self.assertTrue(all(len(t['tests']) == 5 for t in coding.TASKS))
        self.assertTrue(coding.QUICK_TASK_IDS <= {t['id'] for t in coding.TASKS})

    def test_revised_task_answers_with_reference_implementations(self):
        for task in coding.TASKS:
            if task['id'] not in ('M2', 'X1', 'X2', 'X4'):
                continue
            with self.subTest(task=task['id']):
                outcomes, error, details = coding.evaluate_code(REFERENCE_CODE, task)
                self.assertIsNone(error)
                self.assertEqual(outcomes, [True] * 5, details)

    def test_mutation_is_detected_even_with_correct_return_value(self):
        task = {'name': 'f', 'tests': [(([1],), [1])], 'immutable_args': [0]}
        outcomes, _, details = coding.evaluate_code('def f(x):\n    x.append(2)\n    return [1]', task)
        self.assertEqual(outcomes, [False])
        self.assertFalse(details[0]['inputs_unchanged'])

    def test_booleans_do_not_pass_as_integers(self):
        task = {'name': 'f', 'tests': [((), 1)]}
        self.assertEqual(coding.evaluate_code('def f(): return True', task)[0], [False])

    def test_float_rounding_tolerance(self):
        task = {'name': 'f', 'tests': [((), {'average': 5 / 3})]}
        self.assertEqual(coding.evaluate_code("def f(): return {'average': 1.666666667}", task)[0], [True])


class ReasoningContextAuditTests(unittest.TestCase):
    def test_reasoning_distribution_is_preserved(self):
        self.assertEqual(len(reasoning.TASKS), 12)
        self.assertEqual(Counter(t[1] for t in reasoning.TASKS), {'easy': 3, 'medium': 4, 'hard': 5})
        self.assertEqual(reasoning.TASKS[0][3], str(3 * 2**4))

    def test_answer_parser_does_not_cherry_pick(self):
        self.assertEqual(reasoning.extract_final_answer('FINAL_ANSWER: Ben'), 'Ben')
        self.assertEqual(reasoning.extract_final_answer('```text\nFINAL_ANSWER: 48\n```'), '48')
        self.assertIsNone(reasoning.extract_final_answer('FINAL_ANSWER: 9\nFINAL_ANSWER: 48'))
        self.assertIsNone(reasoning.extract_final_answer('First I tried 48, then calculated 49.'))

    def test_six_context_documents_have_unique_target_and_decoys(self):
        self.assertEqual(len(context.LEVELS), 6)
        self.assertEqual([n for _, n in context.LEVELS], [350, 700, 1200, 2000, 3500, 5000])
        for task_id, size in context.LEVELS:
            document, code = context.make_document(task_id, size)
            self.assertEqual((document, code), context.make_document(task_id, size))
            self.assertEqual(document.count(f'RECORD_{task_id}_ACCESS_CODE='), 1)
            self.assertIn(f'RECORD_{task_id}_ACCESS_CODE={code}', document)
            self.assertEqual(len(re.findall(r'RECORD_D\d+_ACCESS_CODE=', document)), 3)
            self.assertEqual(len(code), 10)


class TransportAndScoreTests(unittest.TestCase):
    def test_bad_payloads_are_rejected(self):
        client = Mock()
        for payload in ({'response': 3, 'done': True}, {'response': 'answer'}, [],
                        {'response': 'answer', 'done': True, 'error': 'failed'}):
            with self.subTest(payload=payload):
                client.post.return_value.json.return_value = payload
                with self.assertRaises(ValueError):
                    common.generate_response('http://localhost:11434', 'model', 'prompt', {}, 5, client)

    def test_network_failures_invalidate_score_but_model_failures_do_not(self):
        for kind, valid in [('request', False), ('model_output', True)]:
            result = {'tasks': [{'error_kind': kind, 'error': 'example'}]}
            common.finish_score(result, 'coding_score', 75)
            self.assertEqual(result['score_valid'], valid)
            self.assertEqual(result['coding_score'], 75 if valid else None)

    def test_speed_metrics_and_invalid_samples(self):
        payload = {'response': 'text', 'done': True, 'eval_count': 256, 'eval_duration': 8_000_000_000}
        with patch('model_speed_test.generate_response', return_value=(payload, 10)):
            self.assertEqual(speed.generate('model', 'url', 'prompt')['tokens_per_second'], 32)
        for tokens, duration in [(0, 1), (31, 100), (True, 100), (256, 0), (256, None)]:
            with self.subTest(tokens=tokens, duration=duration):
                with patch('model_speed_test.generate_response', return_value=({'eval_count': tokens, 'eval_duration': duration}, 1)):
                    with self.assertRaises(ValueError):
                        speed.generate('model', 'url', 'prompt')

    def test_speed_full_sample_required_and_warmup_excluded(self):
        for samples, valid in [([100, 10, 20, 30], True), ([100, 10, ValueError('bad duration'), 30], False)]:
            values = [v if isinstance(v, Exception) else {'tokens_per_second': v} for v in samples]
            with tempfile.TemporaryDirectory() as directory:
                argv = ['model_speed_test.py', '--model', 'model', '--results-dir', directory]
                with patch('sys.argv', argv), patch('model_speed_test.choose_models', return_value=['model']), \
                        patch('model_speed_test.generate', side_effect=values), redirect_stdout(StringIO()):
                    speed.main()
                result = json.loads(next(Path(directory).glob('**/*.json')).read_text())
            self.assertEqual(result['score_valid'], valid)
            self.assertEqual(result['speed_score'], 100 if valid else None)
            self.assertEqual(len(result['runs']), 3 if valid else 2)
            self.assertEqual(result['median_tokens_per_second'], 20)

    def test_renamed_module_runs_through_interactive_dispatch(self):
        answers = [t[3] for t in reasoning.TASKS if t[0] in reasoning.QUICK_IDS]
        with tempfile.TemporaryDirectory() as directory:
            with patch('model_reasoning_test.choose_models', return_value=['model']), \
                    patch('model_reasoning_test.ask', side_effect=[(f'FINAL_ANSWER: {a}', .1, 'stop') for a in answers]), \
                    redirect_stdout(StringIO()):
                summary, interrupted = run_test('reasoning', 'model', 'url', 'quick', Path(directory))
            result = json.loads(summary['files'][0].read_text())
        self.assertFalse(interrupted)
        self.assertEqual(summary['status'], 'completed')
        self.assertEqual(result['reasoning_score'], 100)
        self.assertEqual(result['benchmark_version'], 'reasoning-v4')

    def test_all_four_suites_from_one_menu_with_fake_ollama(self):
        def post(url, json, timeout):
            prompt = json['prompt']
            if prompt.startswith('Implement a Python function'):
                name = re.search(r'named (\w+)', prompt).group(1)
                answer = REFERENCE_CODE if name in ('remove_adjacent_groups', 'apply_atomic_batches',
                                                     'apply_patch', 'aggregate_logs') else f'def {name}(*args): return None'
            elif 'Question: ' in prompt:
                question = prompt.split('Question: ', 1)[1].strip()
                answer = 'FINAL_ANSWER: ' + next(t[3] for t in reasoning.TASKS if t[2] == question)
            elif prompt.startswith('Find the value of RECORD_'):
                record = re.search(r'Find the value of (RECORD_K\d+_ACCESS_CODE)', prompt).group(1)
                answer = re.search(record + r'=([A-Z2-9]{10})', prompt).group(1)
            else:
                answer = 'A synthetic speed response.'
            response = Mock()
            response.json.return_value = {'response': answer, 'done': True, 'done_reason': 'stop',
                                          'eval_count': 256, 'eval_duration': 8_000_000_000,
                                          'prompt_eval_count': 100}
            return response

        tags = Mock()
        tags.json.return_value = {'models': [{'name': 'synthetic-model:1b'}]}
        with tempfile.TemporaryDirectory() as directory:
            with patch('sys.argv', ['run_benchmarks.py', '--results-dir', directory]), \
                    patch('builtins.input', side_effect=['1', '0', '2']), \
                    patch('benchmark_common.requests.get', return_value=tags), \
                    patch('benchmark_common.requests.post', side_effect=post), \
                    patch('requests.Session.post', side_effect=post), redirect_stdout(StringIO()):
                code = runner_main()
            results = [json.loads(p.read_text()) for p in Path(directory).glob('**/*.json')]
        self.assertEqual(code, 0)
        self.assertEqual(len(results), 4)
        by_version = {r['benchmark_version']: r for r in results}
        self.assertEqual(len(by_version['coding-v3']['tasks']), 20)
        self.assertEqual(len(by_version['reasoning-v4']['tasks']), 12)
        self.assertEqual(len(by_version['context-retrieval-v2']['tasks']), 6)
        self.assertEqual(len(by_version['speed-v2']['runs']), 3)
        self.assertTrue(all(r['score_valid'] for r in results))
        self.assertTrue(all(r['status'] == 'completed' for r in results))
        self.assertEqual(by_version['reasoning-v4']['reasoning_score'], 100)
        self.assertEqual(by_version['context-retrieval-v2']['context_score'], 100)


if __name__ == '__main__':
    unittest.main()
