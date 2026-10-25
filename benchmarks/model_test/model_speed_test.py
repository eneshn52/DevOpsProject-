"""Local generation speed benchmark (speed-v2)."""
import argparse
import math
import statistics
import requests
from benchmark_common import add_cli_args, choose_models, start_run, save, generate_response

BENCHMARK_VERSION = 'speed-v2'
GENERATION_OPTIONS = {'temperature': 0, 'seed': 42, 'num_predict': 256, 'num_ctx': 8192}

# Explicit project policy, NOT a standardized industry rating.
TARGET_TOKENS_PER_SECOND = 20.0  # 20 tok/s => 100 points; configurable
MIN_OUTPUT_TOKENS = 32
PROMPTS = [
    'Write at least 400 words in plain English explaining a task queue: submitting work, '
    'waiting, processing, returning results, and handling retries. Use paragraphs, no code.',
    'Write at least 400 words in plain English explaining a cache: storing results, '
    'looking up entries, expiring data, and handling misses. Use paragraphs, no code.',
    'Write at least 400 words in plain English explaining a dependency graph: representing tasks, '
    'ordering work, detecting cycles, and completing work. Use paragraphs, no code.',
]
WARMUP_PROMPT = ('Write several paragraphs in plain English explaining how a team tracks '
                 'the progress of a long project. Continue for at least 400 words, no code.')

def generate(model, url, prompt):
    data, elapsed = generate_response(url, model, prompt, GENERATION_OPTIONS, timeout=900)
    tokens = data.get('eval_count')
    duration_ns = data.get('eval_duration')
    if type(tokens) is not int or tokens < MIN_OUTPUT_TOKENS:
        raise ValueError(f'Speed sample needs at least {MIN_OUTPUT_TOKENS} output tokens.')
    if type(duration_ns) is not int or duration_ns <= 0:
        raise ValueError('Ollama returned missing or invalid eval_duration.')
    tps=tokens/(duration_ns/1e9)
    return {'output_tokens':tokens,'eval_duration_ns':duration_ns,
            'load_duration_ns':data.get('load_duration',0),
            'total_request_seconds':elapsed, 'tokens_per_second':tps,
            'prompt_tokens': data.get('prompt_eval_count'), 'done_reason': data.get('done_reason')}

def main():
    parser = argparse.ArgumentParser(description='FR-8 speed benchmark')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--target-tps', type=float, default=TARGET_TOKENS_PER_SECOND)
    add_cli_args(parser)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be >= 1')
    if not math.isfinite(args.target_tps) or args.target_tps <= 0:
        parser.error('--target-tps must be a finite positive number')
    try:
        models = choose_models(args)
    except (requests.RequestException, ValueError) as exc:
        parser.error(str(exc))
    for model in models:
        output, run_id = start_run(args.results_dir, model, 'model_speed_test', 'standard')
        print(f'\n=== {model} ===', flush=True)
        runs = []
        result = {'model': model, 'benchmark_version': BENCHMARK_VERSION, 'run_id': run_id,
                  'generation_options': GENERATION_OPTIONS, 'prompts': PROMPTS,
                  'minimum_output_tokens': MIN_OUTPUT_TOKENS, 'requested_repeats': args.repeats,
                  'status': 'running', 'target_tokens_per_second': args.target_tps,
                  'warmup': None, 'runs': runs}
        save(output, result)
        try:
            print('Warmup (not scored)...', flush=True)
            result['warmup'] = generate(model, args.url, WARMUP_PROMPT)
            save(output, result)
        except (requests.RequestException, ValueError) as exc:
            result.update(status='error', error=str(exc), score_valid=False, speed_score=None)
            save(output, result)
            print(f'Warmup ERROR: {exc}\nSaved: {output}', flush=True)
            continue
        for i in range(args.repeats):
            try:
                prompt_index = i % len(PROMPTS)
                run = generate(model, args.url, PROMPTS[prompt_index])
                run.update(run=i + 1, prompt_id=prompt_index + 1)
                runs.append(run)
                print(f'Run {i+1}: {run["tokens_per_second"]:.2f} tok/s', flush=True)
            except (requests.RequestException, ValueError) as exc:
                print(f'Run {i+1}: ERROR {exc}', flush=True)
                result.setdefault('errors', []).append({'run': i+1, 'error': str(exc)})
            save(output, result)
        median = statistics.median(r['tokens_per_second'] for r in runs) if runs else None
        valid = len(runs) == args.repeats
        score = round(min(100, 100 * median / args.target_tps), 2) if valid else None
        result.update(median_tokens_per_second=round(median, 3) if median is not None else None,
                      speed_score=score, score_valid=valid, status='completed' if valid else 'error')
        save(output, result)
        print(f'SPEED SCORE: {score}/100 (target {args.target_tps} tok/s)\nSaved: {output}', flush=True)

if __name__ == '__main__':
    main()
