"""Portable coding tasks and scored generated-code checks (coding-v3)."""
import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from benchmark_common import (add_cli_args, choose_models, start_run, save,
                              generate_response, finish_score)

BENCHMARK_VERSION = 'coding-v3'
GENERATION_OPTIONS = {'temperature': 0, 'seed': 42, 'num_predict': 2048, 'num_ctx': 8192}

WEIGHTS = {'easy': 0.10, 'medium': 0.25, 'hard': 0.35, 'expert': 0.30}
QUICK_TASK_IDS = {'E3', 'E4', 'M2', 'M5', 'M6', 'H1', 'H3', 'H5', 'X1', 'X3'}
# Each test is (positional_args, expected_output); expected_exceptions marks raised-error cases.
TASKS = [{'id': 'E1',
  'level': 'easy',
  'name': 'second_largest',
  'instruction': 'Return the second largest DISTINCT integer in a list, or None if fewer than two distinct '
                 'integers exist.',
  'tests': [(([1, 2, 3],), 2), (([4, 4, 2],), 2), (([],), None), (([-3, -1, -2],), -2), (([7],), None)]},
 {'id': 'E2',
  'level': 'easy',
  'name': 'is_balanced',
  'instruction': 'Return True if (), [] and {} in a string are properly balanced and nested. Ignore all '
                 'other characters.',
  'tests': [(('([])',), True), (('([)]',), False), (('',), True), (('abc',), True), (('(((',), False)]},
 {'id': 'E3',
  'level': 'easy',
  'name': 'word_frequency',
  'instruction': 'Count case-insensitive ASCII-letter-only words in text, treating nonletters as separators. '
                 'Return a dictionary with lowercase words.',
  'tests': [(('Hi hi!',), {'hi': 2}),
            (('a-b a',), {'a': 2, 'b': 1}),
            (('',), {}),
            (('One TWO one',), {'one': 2, 'two': 1}),
            (('42 abc 42',), {'abc': 1})]},
 {'id': 'E4',
  'level': 'easy',
  'name': 'merge_sorted',
  'instruction': 'Merge two ascending sorted integer lists into one ascending list, preserving all '
                 'duplicates and without changing the inputs.',
  'tests': [(([1, 3], [2, 4]), [1, 2, 3, 4]),
            (([], [1]), [1]),
            (([1, 1], [1, 2]), [1, 1, 1, 2]),
            (([-2, 0], [-3, 2]), [-3, -2, 0, 2]),
            (([], []), [])],
  'immutable_args': [0, 1]},
 {'id': 'M1',
  'level': 'medium',
  'name': 'binary_search_first',
  'instruction': 'Given ascending sorted integer list nums and target, return the FIRST index of target or '
                 '-1. Aim for O(log n).',
  'tests': [(([1, 2, 2, 3], 2), 1),
            (([1, 2, 3], 4), -1),
            (([], 3), -1),
            (([5, 5, 5], 5), 0),
            (([-4, -1, 0], -1), 1)]},
 {'id': 'M2',
  'level': 'medium',
  'name': 'remove_adjacent_groups',
  'instruction': 'Given a string text and positive integer k, repeatedly remove adjacent groups of exactly k '
                 'identical characters until no such group remains. A longer run loses k characters at a '
                 'time. Return the remaining string. Process cascading removals. Use a stack or equivalent '
                 'in-memory algorithm; no files or operating-system APIs.',
  'tests': [(('abbaca', 2), 'ca'),
            (('deeedbbcccbdaa', 3), 'aa'),
            (('abc', 3), 'abc'),
            (('abccba', 2), ''),
            (('aaa', 1), '')]},
 {'id': 'M3',
  'level': 'medium',
  'name': 'group_anagrams',
  'instruction': 'Group lowercase ASCII words that are anagrams. Return a list of groups. Preserve the order '
                 'of first appearance of each group and the original order within groups.',
  'tests': [((['eat', 'tea', 'tan', 'ate', 'nat', 'bat'],), [['eat', 'tea', 'ate'], ['tan', 'nat'], ['bat']]),
            (([],), []),
            ((['a', 'a'],), [['a', 'a']]),
            ((['ab', 'ba', 'abc'],), [['ab', 'ba'], ['abc']]),
            ((['', 'b', ''],), [['', ''], ['b']])]},
 {'id': 'M4',
  'level': 'medium',
  'name': 'validate_brackets_with_quotes',
  'instruction': 'Check balanced (), [], {} outside double-quoted strings. Inside quotes, brackets are '
                 'ignored. A backslash escapes the next character only inside quotes. Return False for an '
                 'unterminated quote.',
  'tests': [(('("[")',), True),
            (('("[")]',), False),
            (('"["',), True),
            (('"abc',), False),
            (('("a\\"b")',), True)]},
 {'id': 'M5',
  'level': 'medium',
  'name': 'top_k_frequent',
  'instruction': 'Given integers nums and nonnegative k, return up to k distinct integers ordered by '
                 'decreasing frequency, ties by increasing numeric value.',
  'tests': [(([1, 1, 2, 2, 3], 2), [1, 2]),
            (([4, 4, 3, 3, 2], 2), [3, 4]),
            (([], 3), []),
            (([5, 5, 5], 0), []),
            (([-1, -1, 0, 0, 2], 5), [-1, 0, 2])]},
 {'id': 'M6',
  'level': 'medium',
  'name': 'longest_unique_substring',
  'instruction': 'Return the length of the longest substring without repeated characters.',
  'tests': [(('abcabcbb',), 3), (('bbbbb',), 1), (('',), 0), (('pwwkew',), 3), (('abba',), 2)]},
 {'id': 'H1',
  'level': 'hard',
  'name': 'shortest_path',
  'instruction': 'Given n nodes numbered 0..n-1, directed weighted edges [u,v,w] with nonnegative integer '
                 'weights, source s and destination t, return shortest distance or -1 if unreachable.',
  'tests': [((3, [[0, 1, 2], [1, 2, 3], [0, 2, 10]], 0, 2), 5),
            ((2, [], 0, 1), -1),
            ((1, [], 0, 0), 0),
            ((4, [[0, 1, 1], [0, 2, 1], [1, 3, 2], [2, 3, 1]], 0, 3), 2),
            ((3, [[0, 1, 0], [1, 2, 0]], 0, 2), 0)]},
 {'id': 'H2',
  'level': 'hard',
  'name': 'min_window',
  'instruction': 'Return the shortest substring of s containing all characters of t including multiplicity. '
                 'Return empty string if impossible. If multiple shortest windows exist, choose the '
                 'earliest.',
  'tests': [(('ADOBECODEBANC', 'ABC'), 'BANC'),
            (('a', 'aa'), ''),
            (('aa', 'aa'), 'aa'),
            (('abc', ''), ''),
            (('abdcab', 'ab'), 'ab')]},
 {'id': 'H3',
  'level': 'hard',
  'name': 'evaluate_expression',
  'instruction': 'Evaluate a string containing integers, whitespace, +, -, *, / and parentheses. Use '
                 'standard precedence; division truncates toward zero. Unary + and - must work. Return an '
                 'integer. Inputs are valid and no division by zero.',
  'tests': [(('2+3*4',), 14),
            ((' (2+3)*4 ',), 20),
            (('-7/2',), -3),
            (('2*-3+10',), 4),
            (('18/(2+1)-4',), 2)]},
 {'id': 'H4',
  'level': 'hard',
  'name': 'edit_distance',
  'instruction': 'Return Levenshtein edit distance between strings a and b (insert, delete, substitute each '
                 'cost 1).',
  'tests': [(('kitten', 'sitting'), 3),
            (('', 'abc'), 3),
            (('abc', 'abc'), 0),
            (('flaw', 'lawn'), 2),
            (('intention', 'execution'), 5)]},
 {'id': 'H5',
  'level': 'hard',
  'name': 'course_order',
  'instruction': 'Given n courses 0..n-1 and prerequisite pairs [course, prerequisite], return '
                 'lexicographically smallest valid topological order, or [] if a cycle exists.',
  'tests': [((3, [[2, 0], [2, 1]]), [0, 1, 2]),
            ((2, [[0, 1], [1, 0]]), []),
            ((3, []), [0, 1, 2]),
            ((4, [[1, 0], [2, 0], [3, 1], [3, 2]]), [0, 1, 2, 3]),
            ((1, [[0, 0]]), [])]},
 {'id': 'H6',
  'level': 'hard',
  'name': 'max_sliding_window',
  'instruction': 'Given list nums and integer k with 1 <= k <= len(nums), return maximum in each contiguous '
                 'window of length k. Aim for O(n).',
  'tests': [(([1, 3, -1, -3, 5, 3, 6, 7], 3), [3, 3, 5, 5, 6, 7]),
            (([1], 1), [1]),
            (([9, 8, 7], 2), [9, 8]),
            (([-1, -2, -3], 3), [-1]),
            (([4, 4, 4], 1), [4, 4, 4])]},
 {'id': 'X1',
  'level': 'expert',
  'name': 'apply_atomic_batches',
  'instruction': 'Given initial, a dict of string account names to nonnegative integer balances, and '
                 'batches, a list of lists of operations, apply each batch atomically. Each operation must '
                 'be a two-item list [account, delta], where account exists and delta has exact type int '
                 '(bool is invalid). Apply deltas sequentially. If an operation is malformed, references a '
                 'missing account, or would make a balance negative, roll back the ENTIRE current batch and '
                 'continue with the next batch. Empty batches are valid. Return ONLY the final balances '
                 'dictionary. Do not mutate initial.',
  'tests': [(({'a': 5, 'b': 2}, [[['a', -3], ['b', 3]]]), {'a': 2, 'b': 5}),
            (({'a': 5}, [[['a', -2], ['a', -4]], [['a', 1]]]), {'a': 6}),
            (({'a': 5}, [[['a', 2], ['missing', 1]]]), {'a': 5}),
            (({'a': 2}, [[['a', -3], ['a', 3]]]), {'a': 2}),
            (({'a': 4}, [[['a', True]], [['a']], [['a', -1]]]), {'a': 3})],
  'immutable_args': [0]},
 {'id': 'X2',
  'level': 'expert',
  'name': 'apply_patch',
  'instruction': 'Given a list of text lines and a list of operations, apply sequentially: ["insert", index, '
                 'text], ["delete", index], ["replace", index, text]. Each operation must be a list with '
                 'exactly the shown number of items; kind must be one of these three strings, index must '
                 'have exact type int (not bool), and text must be str. Insert accepts 0 <= index <= current '
                 'length; delete/replace accept 0 <= index < current length. Raise ValueError for any '
                 'invalid operation or index. Return new lines without mutating the original, including when '
                 'an error occurs.',
  'tests': [((['a', 'b'], [['insert', 1, 'x'], ['delete', 0], ['replace', 1, 'z']]), ['x', 'z']),
            (([], [['insert', 0, 'x']]), ['x']),
            ((['a', 'b'], [['delete', 0], ['insert', 1, 'c']]), ['b', 'c']),
            ((['a'], [['replace', 0, 'z'], ['delete', 2]]), None),
            ((['a'], [['insert', 0]]), None)],
  'expected_exceptions': {3: 'ValueError', 4: 'ValueError'},
  'immutable_args': [0]},
 {'id': 'X3',
  'level': 'expert',
  'name': 'resolve_dependencies',
  'instruction': 'Given dict mapping package to list of direct dependencies, return a list with dependencies '
                 'before dependents, lexicographically smallest among currently available packages. '
                 'Dependencies missing as keys are still packages with no dependencies. If any cycle exists, '
                 'return [].',
  'tests': [(({'app': ['lib'], 'lib': []},), ['lib', 'app']),
            (({'a': ['b'], 'b': ['a']},), []),
            (({'z': [], 'a': []},), ['a', 'z']),
            (({'app': ['x', 'y'], 'y': ['x']},), ['x', 'y', 'app']),
            (({},), [])]},
 {'id': 'X4',
  'level': 'expert',
  'name': 'aggregate_logs',
  'instruction': 'Given a list of records, group valid records by service. A valid record is a list of '
                 'exactly four items [timestamp, service, status, latency_ms]: timestamp and status must '
                 'have exact type int, service must be a nonempty str, and latency_ms must have exact type '
                 'int and be nonnegative. Booleans are invalid integer fields. Ignore invalid records. '
                 'Return a dictionary mapping service to {"count": int, "errors": int, "avg_latency": '
                 'float}; errors counts status >= 500 and avg_latency is the arithmetic mean (floating-point '
                 'tolerance 1e-9).',
  'tests': [(([[1, 'api', 200, 10], [2, 'api', 500, 30]],),
             {'api': {'count': 2, 'errors': 1, 'avg_latency': 20.0}}),
            (([],), {}),
            (([[1, 'a', 503, 0], [2, 'b', 404, 8]],),
             {'a': {'count': 1, 'errors': 1, 'avg_latency': 0.0},
              'b': {'count': 1, 'errors': 0, 'avg_latency': 8.0}}),
            (([None,
               [1, 'a', 200],
               [1, 'a', 200, 1, 2],
               [True, 'a', 200, 1],
               [1, 'a', True, 1],
               [1, 'a', 200, False],
               [1, 'a', 200, -1],
               [1, 'a', 200, 10]],),
             {'a': {'count': 1, 'errors': 0, 'avg_latency': 10.0}}),
            (([[1, '', 200, 2], ['bad', 'x', 500, 3], [2, 'x', 500, 1], [3, 'x', 200, 2], [4, 'x', 200, 2]],),
             {'x': {'count': 3, 'errors': 1, 'avg_latency': 1.6666666666666667}})]}]


def extract_code(response, name):
    blocks = re.findall(r'```(?:python)?\s*(.*?)```', response, flags=re.S | re.I)
    for candidate in blocks + [response]:
        try:
            tree = ast.parse(candidate)
        except SyntaxError:
            continue
        if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name for n in tree.body):
            continue
        # Retain imports, constants and helper functions; strip top-level execution.
        retained_nodes = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign))]
        return ast.unparse(ast.Module(body=retained_nodes, type_ignores=[]))
    return None


CASE_RUNNER = '''
def equal(actual, expected):
    if isinstance(expected, float):
        return type(actual) in (int, float) and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(equal(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    return actual == expected

details = []
for index, (args, expected) in enumerate(tests):
    before = copy.deepcopy(args)
    actual, exception = None, None
    try:
        actual = globals()[function_name](*args)
    except Exception as exc:
        exception = type(exc).__name__
    expected_exception = expected_exceptions.get(index)
    correct = (exception == expected_exception if expected_exception else
               exception is None and equal(actual, expected))
    unchanged = all(args[i] == before[i] for i in immutable_args)
    details.append({'passed': bool(correct and unchanged), 'actual': repr(actual)[:500],
                    'expected': repr(expected)[:500], 'exception': exception,
                    'expected_exception': expected_exception, 'inputs_unchanged': unchanged})
print('FR8_RESULTS=' + json.dumps(details))
'''


def evaluate_code(code, task):
    # CAUTION: process isolation and timeout are not a security sandbox.
    runner = (f'import json, copy, math\n{code}\n'
              f'tests = {task["tests"]!r}\nfunction_name = {task["name"]!r}\n'
              f'expected_exceptions = {task.get("expected_exceptions", {})!r}\n'
              f'immutable_args = {task.get("immutable_args", [])!r}\n' + CASE_RUNNER)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'runner.py'
        path.write_text(runner, encoding='utf-8')
        try:
            completed = subprocess.run([sys.executable, '-I', '-S', str(path)], capture_output=True, text=True, timeout=8, cwd=directory)
        except subprocess.TimeoutExpired:
            return [False]*len(task['tests']), 'Generated code timed out.', []
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith('FR8_RESULTS='):
            try:
                details=json.loads(line.split('=',1)[1])
                if (isinstance(details, list) and len(details) == len(task['tests'])
                        and all(isinstance(x, dict) and type(x.get('passed')) is bool for x in details)):
                    return [row['passed'] for row in details], None, details
            except ValueError:
                pass
    return [False]*len(task['tests']), (completed.stderr or 'invalid result')[-500:], []


def generate(model, task, url):
    prompt = (f'Implement a Python function named {task["name"]}.\n'
              f'Specification: {task["instruction"]}\n'
              'Return only valid Python code. Include required imports and helpers. '
              'Do not include example calls or markdown. Standard library only. '
              'Use in-memory computation only; do not access files, the network, or shell commands.')
    return generate_response(url, model, prompt, GENERATION_OPTIONS, timeout=600)


def main():
    parser = argparse.ArgumentParser(description='FR-8 coding benchmark')
    parser.add_argument('--mode', choices=['quick', 'full'], default='quick')
    add_cli_args(parser)
    args = parser.parse_args()
    try:
        models = choose_models(args)
    except (requests.RequestException, ValueError) as exc:
        parser.error(str(exc))
    selected_tasks = [t for t in TASKS if args.mode == 'full' or t['id'] in QUICK_TASK_IDS]
    for model in models:
        output_path, run_id = start_run(args.results_dir, model, 'model_coding_test', args.mode)
        print(f'\n=== {model} ({args.mode}) | {len(selected_tasks)} tasks ===', flush=True)
        rows = []
        result = {'model': model, 'benchmark_version': BENCHMARK_VERSION, 'mode': args.mode,
                  'generation_options': GENERATION_OPTIONS, 'level_weights': WEIGHTS,
                  'run_id': run_id, 'status': 'running', 'tasks': rows}
        save(output_path, result)
        for task in selected_tasks:
            print(f'{task["id"]} {task["name"]}...', end=' ', flush=True)
            row = {'task': task['id'], 'level': task['level'], 'name': task['name'], 'total': len(task['tests'])}
            try:
                data, elapsed = generate(model, task, args.url)
                code = extract_code(data['response'], task['name'])
                outcomes, error, details = evaluate_code(code, task) if code else ([False]*len(task['tests']), 'function not found', [])
                row.update(passed=sum(outcomes), outcomes=outcomes, error=error, code=code,
                           error_kind='model_output' if error else None, case_results=details,
                           raw_response=data['response'], elapsed_seconds=elapsed,
                           done_reason=data.get('done_reason'),
                           output_tokens=data.get('eval_count', 0), eval_duration_ns=data.get('eval_duration', 0),
                           load_duration_ns=data.get('load_duration', 0))
                print(f'{row["passed"]}/{row["total"]} ({elapsed}s)', flush=True)
            except (requests.RequestException, ValueError) as exc:
                row.update(passed=0, error=str(exc), error_kind='request')
                print(f'REQUEST ERROR: {exc}', flush=True)
            rows.append(row)
            save(output_path, result)
        breakdown = {}
        raw_breakdown = {}
        for level, weight in WEIGHTS.items():
            selected = [r for r in rows if r['level'] == level]
            passed = sum(r.get('passed', 0) for r in selected)
            total = sum(r['total'] for r in selected)
            raw_breakdown[level] = 100 * passed / total if total else 0.0
            breakdown[level] = round(raw_breakdown[level], 2)
        score = round(sum(WEIGHTS[level] * raw_breakdown[level] for level in WEIGHTS), 2)
        finish_score(result, 'coding_score', score)
        result['level_scores'] = breakdown if result['score_valid'] else None
        save(output_path, result)
        print(f'CODING SCORE: {result["coding_score"]}/100 | {result["level_scores"]}\nSaved: {output_path}', flush=True)

if __name__ == '__main__':
    main()
