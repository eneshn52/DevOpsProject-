"""Shared Ollama model selection and versioned, per-model result storage."""
import json
import os
import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path
import requests

DEFAULT_URL = os.getenv('OLLAMA_DEFAULT_URL', 'http://localhost:11434')


def generate_response(url, model, prompt, options, timeout, session=None):
    """Reject malformed transport responses instead of scoring them as wrong answers."""
    client = session or requests
    started = time.perf_counter()
    response = client.post(f'{url.rstrip("/")}/api/generate', json={
        'model': model, 'prompt': prompt, 'stream': False, 'options': options,
    }, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get('response'), str):
        raise ValueError('Ollama response must contain a response string.')
    if data.get('error') or data.get('done') is not True:
        raise ValueError('Ollama did not return a completed generation.')
    return data, round(time.perf_counter() - started, 3)


def finish_score(result, key, score):
    """Infrastructure failures invalidate a score; wrong model answers still count."""
    errors = [row for row in result.get('tasks', [])
              if row.get('error_kind') == 'request']
    result.update(status='error' if errors else 'completed', score_valid=not errors)
    result[key] = None if errors else score

def add_cli_args(parser):
    parser.add_argument('--url', default=DEFAULT_URL, help='Ollama server URL')
    parser.add_argument('--model', help='Exact model name (skips interactive menu)')
    parser.add_argument('--all', action='store_true', help='Benchmark all installed models')
    parser.add_argument('--results-dir', default=str(Path(__file__).resolve().parents[2] / 'benchmark'), help='Root directory for results')

def installed_models(url):
    response = requests.get(f'{url.rstrip("/")}/api/tags', timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise ValueError('Ollama /api/tags returned an invalid model list.')
    return sorted({m['name'] for m in data['models']
                   if isinstance(m, dict) and isinstance(m.get('name'), str) and m['name']})

def choose_models(args):
    if args.model and args.all:
        raise ValueError('Use either --model or --all, not both')
    models = installed_models(args.url)
    if not models:
        raise ValueError('No models found on Ollama. Install one with: ollama pull MODEL')
    if args.model:
        if args.model not in models:
            raise ValueError(f'Model {args.model!r} is not installed. Available: {", ".join(models)}')
        return [args.model]
    if args.all:
        return models
    print('\nAvailable Ollama models:')
    for i, name in enumerate(models, 1):
        print(f'  {i}. {name}')
    print('  a. All models')
    while True:
        try:
            selection = input('\nSelect model (number or a): ').strip().lower()
        except EOFError as exc:
            raise ValueError('Interactive input unavailable; use --model NAME or --all') from exc
        if selection in ('a', 'all'):
            return models
        if selection.isdigit() and 1 <= int(selection) <= len(models):
            return [models[int(selection)-1]]
        print('Invalid selection. Try again.')

def start_run(results_dir, model, benchmark, mode):
    slug = re.sub(r'[^A-Za-z0-9._-]+', '_', model).strip('._') or 'model'
    folder = Path(results_dir) / slug
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = folder / f'{benchmark}_{mode}_{stamp}.json'
    return path, stamp

def save(path, result):
    result.setdefault('environment', {'os': platform.system(), 'architecture': platform.machine(),
                                      'python': platform.python_version()})
    result.setdefault('started_at', datetime.now(timezone.utc).isoformat())
    result['updated_at'] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)
