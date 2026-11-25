"""FR-9: master-triggered benchmark runs executed through the FR-16 worker channel."""
import ast
import random
import re
import statistics
import string

from django.db import transaction
from django.utils import timezone

from .capabilities import METRICS, save_scores
from .models import Agent, BenchmarkCase, BenchmarkRun, Task

SPEED_TARGET_TOKENS_PER_SECOND = 20.0  # Explicit project policy, not an industry standard.


def _cases(context_window):
    coding = [
        ('add', 'Write a Python function add(a, b) that returns a + b.', 'def add(a, b):\n    return a + b'),
        ('is_even', 'Write a Python function is_even(n) that returns whether n is even.', 'def is_even(n):\n    return n % 2 == 0'),
        ('maximum', 'Write a Python function maximum(a, b, c) that returns the largest value.', 'def maximum(a, b, c):\n    return max(a, b, c)'),
        ('reverse', 'Write a Python function reverse_text(text) that returns the text reversed.', 'def reverse_text(text):\n    return text[::-1]'),
    ]
    reasoning = [
        ('sequence', 'A sequence starts at 3. Each next term is twice the previous. What is term five?', '48'),
        ('logic', 'All tulips are flowers. Some flowers are red. Must some tulips be red? Answer YES or NO.', 'NO'),
        ('percentage', 'A price of 250 increases by 20%, then the new price decreases by 20%. Final price?', '240'),
        ('probability', 'A bag has 3 red and 2 blue balls. Two are drawn without replacement. Probability both red? Give a reduced fraction.', '3/10'),
    ]
    # Context tiers scale with the registered context window; the worker enforces its 80% budget.
    context = []
    alphabet = string.ascii_uppercase + string.digits
    for index, fraction in enumerate((0.06, 0.15, 0.25), 1):
        rng = random.Random(9100 + index)
        target = ''.join(rng.choice(alphabet) for _ in range(10))
        decoys = [''.join(rng.choice(alphabet) for _ in range(10)) for _ in range(3)]
        approx_bytes = max(300, int(context_window * fraction))
        filler_count = max(20, approx_bytes // 6)
        filler = ['worker queue status request result network memory service'] * (filler_count // 8 + 1)
        words = ' '.join(filler).split()[:filler_count]
        words.insert(len(words) // 5, f'RECORD_DECOY_1_ACCESS={decoys[0]}')
        words.insert(len(words) // 2, f'RECORD_DECOY_2_ACCESS={decoys[1]}')
        words.insert(len(words) * 4 // 5, f'RECORD_DECOY_3_ACCESS={decoys[2]}')
        words.insert(len(words) // 2, f'RECORD_TARGET_ACCESS={target}')
        prompt = ('Find RECORD_TARGET_ACCESS in the document. Reply with ONLY its 10-character value.\n'
                  'DOCUMENT: ' + ' '.join(words) + '\nEND DOCUMENT. What is RECORD_TARGET_ACCESS?')
        context.append((f'tier_{index}', prompt, target))
    speed = [
        ('queue', 'In plain English, describe how a task queue submits, waits, processes, returns results, and retries work. Write at least 120 words.'),
        ('cache', 'In plain English, describe how a software cache stores, looks up, expires, and refreshes data. Write at least 120 words.'),
        ('http', 'In plain English, describe an HTTP request and response, including method, status code, headers, and body. Write at least 120 words.'),
    ]
    return ([("coding", case_id, prompt, {"source": expected}, 256) for case_id, prompt, expected in coding]
            + [("reasoning", case_id, prompt + '\nReturn exactly: FINAL_ANSWER: <answer>', {"answer": expected}, 128)
               for case_id, prompt, expected in reasoning]
            + [("context", case_id, prompt, {"answer": expected}, 64) for case_id, prompt, expected in context]
            + [("speed", case_id, prompt, {}, 256) for case_id, prompt in speed])


def start_benchmark(agent):
    """Create and assign a complete suite atomically. The worker must already be online and idle."""
    with transaction.atomic():
        agent = Agent.objects.select_for_update().select_related('ai_model').get(pk=agent.pk)
        if agent.role == 'MASTER':
            raise ValueError('Choose a specialist worker, not the master agent.')
        if not agent.ai_model:
            raise ValueError('Configure an AI model for this worker first.')
        if not agent.is_online():
            raise ValueError('The worker is offline. Start its FR-16 worker process first.')
        if Task.objects.filter(assigned_agent=agent, status__in=('ASSIGNED', 'IN_PROGRESS')).exists():
            raise ValueError('The worker already has an assigned or running task; wait until it is idle.')
        context_window = agent.context_window or agent.ai_model.max_context or 4096
        if context_window < 1024:
            raise ValueError('The configured context window must be at least 1024 tokens for this suite.')

        run = BenchmarkRun.objects.create(agent=agent, model_name=agent.ai_model.ollama_name)
        for metric, case_id, prompt, expected, max_tokens in _cases(context_window):
            task = Task.objects.create(
                assigned_agent=agent, type='GENERAL', title=f'[FR-9 {run.pk}] {metric}: {case_id}',
                instructions=prompt, expected_output='', max_tokens=max_tokens,
                est_tokens=max(128, min(context_window // 2, len(prompt.encode()) // 4)),
                status='ASSIGNED')
            BenchmarkCase.objects.create(run=run, task=task, metric=metric, case_id=case_id, expected=expected)
        return run


def _clean_code(text):
    text = text.strip()
    match = re.fullmatch(r'```(?:python)?\s*\n(.*?)\n```', text, re.S | re.I)
    return match.group(1).strip() if match else text


def _passed(case, output):
    expected = case.expected
    if case.metric == 'coding':
        try:
            actual_tree = ast.dump(ast.parse(_clean_code(output)), include_attributes=False)
            expected_tree = ast.dump(ast.parse(expected['source']), include_attributes=False)
            return actual_tree == expected_tree
        except (SyntaxError, KeyError, TypeError):
            return False
    if case.metric == 'reasoning':
        match = re.fullmatch(r'FINAL_ANSWER\s*:\s*(.+?)\s*[.!]?\s*', output.strip(), re.I)
        answer = match.group(1).strip() if match else output.strip()
        return answer.casefold() == expected.get('answer', '').casefold()
    if case.metric == 'context':
        return output.strip() == expected.get('answer')
    return True


def record_case_result(task, execution):
    """Record one worker response and atomically finalize scores only after the whole suite passes."""
    case = (BenchmarkCase.objects.select_for_update().select_related('run', 'run__agent')
            .filter(task=task).first())
    if case is None or case.run.status != 'RUNNING' or case.completed_at:
        return
    if execution.status != 'COMPLETED':
        case.outcome = {'passed': False, 'error': execution.error or 'Worker execution failed.'}
        case.completed_at = timezone.now()
        case.save(update_fields=['outcome', 'completed_at'])
        _fail_run(case.run, f'{case.metric}/{case.case_id} could not execute: {case.outcome["error"]}')
        return

    raw = execution.result if isinstance(execution.result, dict) else {}
    generation_s = raw.get('generation_duration_s', 0)
    tokens = raw.get('output_tokens', 0)
    passed = _passed(case, execution.output)
    outcome = {'passed': passed, 'output': execution.output[:4000], 'output_tokens': tokens}
    if case.metric == 'speed':
        if type(generation_s) not in (int, float) or generation_s <= 0 or type(tokens) is not int or tokens < 32:
            case.outcome = {**outcome, 'passed': False, 'error': 'Speed sample needs at least 32 output tokens and Ollama generation timing.'}
            case.completed_at = timezone.now()
            case.save(update_fields=['outcome', 'completed_at'])
            _fail_run(case.run, f'speed/{case.case_id} did not produce a valid timing sample.')
            return
        outcome['tokens_per_second'] = tokens / generation_s
        outcome['passed'] = True
    case.outcome = outcome
    case.completed_at = timezone.now()
    case.save(update_fields=['outcome', 'completed_at'])

    cases = list(case.run.cases.all())
    if not all(item.completed_at for item in cases):
        return

    scores = {}
    for metric in METRICS:
        rows = [item for item in cases if item.metric == metric]
        if metric == 'speed':
            median_tps = statistics.median(item.outcome['tokens_per_second'] for item in rows)
            scores[metric] = round(min(100.0, 100.0 * median_tps / SPEED_TARGET_TOKENS_PER_SECOND), 2)
        else:
            scores[metric] = round(100.0 * sum(item.outcome['passed'] for item in rows) / len(rows), 2)
    # Both profile update and run completion are in the caller's transaction.
    save_scores(case.run.agent, scores)
    case.run.scores = scores
    case.run.details = {'case_count': len(cases), 'passed': sum(bool(i.outcome.get('passed')) for i in cases),
                        'speed_target_tokens_per_second': SPEED_TARGET_TOKENS_PER_SECOND,
                        'suite_version': case.run.suite_version}
    case.run.status = 'COMPLETED'
    case.run.completed_at = timezone.now()
    case.run.save(update_fields=['scores', 'details', 'status', 'completed_at'])


def _fail_run(run, message):
    """Keep old scores and prevent remaining queued benchmark tasks from occupying the worker."""
    run.status = 'FAILED'
    run.error = message
    run.completed_at = timezone.now()
    run.save(update_fields=['status', 'error', 'completed_at'])
    Task.objects.filter(benchmark_case__run=run, status='ASSIGNED').update(status='FAILED')
