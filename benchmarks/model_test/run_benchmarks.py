"""Interactive worker-side FR-8 runner. No master connection is required."""
import argparse
import importlib
import json
from pathlib import Path
import sys

import requests

from benchmark_common import DEFAULT_URL, installed_models, save

TESTS = {'1': 'coding', '2': 'reasoning', '3': 'context', '4': 'speed'}
DEFAULT_RESULTS = Path(__file__).resolve().parents[2] / 'benchmark'


def parse_selection(value):
    parts = [part.strip() for part in value.split(',')]
    if parts == ['0']:
        return list(TESTS.values())
    if not parts or any(part not in TESTS for part in parts):
        raise ValueError('Enter 1-4 separated by commas, or 0 alone for all tests.')
    return list(dict.fromkeys(TESTS[part] for part in parts))


def select_model(models):
    print('\nInstalled local models:')
    for number, name in enumerate(models, 1):
        print(f'  {number}. {name}')
    while True:
        value = input('Select model number: ').strip()
        if value.isdigit() and 1 <= int(value) <= len(models):
            return models[int(value) - 1]
        print('Invalid model number. Try again.')


def select_tests():
    print('\n1. Coding\n2. Reasoning\n3. Context\n4. Speed\n0. All tests')
    while True:
        try:
            return parse_selection(input('Select tests (example: 1,2 or 0): '))
        except ValueError as exc:
            print(exc)


def select_mode():
    while True:
        value = input('Mode: 1 = quick, 2 = full [1]: ').strip().lower()
        if value in ('', '1', 'quick'):
            return 'quick'
        if value in ('2', 'full'):
            return 'full'
        print('Choose 1 or 2.')


def run_test(suite, model, url, mode, results):
    before = set(results.glob('**/*.json'))
    argv = [f'model_{suite}_test.py', '--model', model, '--url', url, '--results-dir', str(results)]
    if suite != 'speed':
        argv.extend(['--mode', mode])
    status, detail, interrupted = 'completed', '', False
    try:
        module = importlib.import_module(f'model_{suite}_test')
        previous_argv = sys.argv
        try:
            sys.argv = argv
            module.main()
        finally:
            sys.argv = previous_argv
    except KeyboardInterrupt:
        status, detail, interrupted = 'interrupted', 'Stopped by user.', True
    except (Exception, SystemExit) as exc:
        status, detail = 'error', str(exc)
    paths = sorted(set(results.glob('**/*.json')) - before)
    for path in paths:
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
            rows = result.get('tasks', []) + result.get('runs', [])
            has_errors = bool(result.get('error') or result.get('errors') or any(
                row.get('error') and row.get('error_kind') != 'model_output' for row in rows))
            if status in ('error', 'interrupted'):
                result.update(status=status, runner_error=detail)
                result['score_valid'] = False
                save(path, result)
            elif result.get('status') != 'completed' or has_errors:
                status = 'error'
                detail = 'Request or execution errors; inspect the JSON details.'
                result['status'] = 'error'
                save(path, result)
        except (ValueError, OSError) as exc:
            status, detail = 'error', f'Cannot read result: {exc}'
    if not paths and status == 'completed':
        status, detail = 'error', 'No result file was created.'
    return {'test': suite, 'status': status, 'detail': detail, 'files': paths}, interrupted


def main():
    parser = argparse.ArgumentParser(description='Select one local model and run multiple FR-8 tests.')
    parser.add_argument('--url', default=DEFAULT_URL)
    parser.add_argument('--results-dir', type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    try:
        models = installed_models(args.url)
        if not models:
            print('No installed models. Install a model with ollama pull MODEL.')
            return 1
        model = select_model(models)
        suites = select_tests()
        mode = select_mode() if any(suite != 'speed' for suite in suites) else 'standard'
        if 'coding' in suites:
            print('\nCoding executes generated Python on this laptop; subprocess isolation is not a security sandbox.')
        summary = []
        for suite in suites:
            print(f'\nStarting {suite}: {model}', flush=True)
            result, interrupted = run_test(suite, model, args.url, mode, args.results_dir.resolve())
            summary.append(result)
            if interrupted:
                break
        print('\nBenchmark summary:')
        for row in summary:
            print(f"  {row['test']}: {row['status']} {row['detail']}")
            for path in row['files']:
                print(f'    {path}')
        if len(summary) < len(suites):
            print('Remaining tests were not started.')
        return 0 if all(row['status'] == 'completed' for row in summary) else 1
    except (requests.RequestException, ValueError) as exc:
        print(f'Cannot list models at {args.url}: {exc}\nCheck that Ollama is running.')
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nSelection cancelled.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
