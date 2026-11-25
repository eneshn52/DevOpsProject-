"""Execute JSON input/output cases in fresh, resource-limited Monty sessions."""
import json
import time
import urllib.request

if __package__:
    from .code_review import review_code
else:
    from code_review import review_code

from pydantic_monty import CollectStreams, Monty, MontyError, MontyRuntimeError, MontySyntaxError

LIMITS = dict(max_memory=8_000_000, max_feed_duration_secs=1, max_recursion_depth=100,
              max_total_sleep_secs=0, max_suspensions=8)


def run_case(pool, source, case):
    started = time.monotonic()
    streams = CollectStreams(max_bytes=4096)
    status, detail = 'error', ''
    try:
        with pool.checkout(limits=LIMITS) as session:
            session.feed_run(source, print_callback=streams)
            # Serialize inside the limited interpreter. A generated object graph can
            # expand dramatically when converted to JSON outside the sandbox.
            actual = session.feed_run(
                'import json\njson.dumps(solve(case_input), sort_keys=True)[:65537]',
                inputs={'case_input': case['input']}, print_callback=streams)
            status = ('passed' if len(actual) <= 65536 and json.loads(actual) == case['expected']
                      and type(json.loads(actual)) is type(case['expected']) else 'failed')
            if status == 'failed':
                detail = repr(actual)[:512]
    except MontySyntaxError as exc:
        status, detail = 'invalid_code', str(exc)
    except MontyRuntimeError as exc:
        status = 'timeout' if isinstance(exc.exception(), TimeoutError) else 'error'
        detail = str(exc)
    except (MontyError, ValueError, TypeError, RecursionError) as exc:
        detail = str(exc)
    return {'status': status, 'duration': time.monotonic() - started, 'detail': detail[:1024],
            **{channel: ''.join(s for name, s in streams.output if name == channel)
               for channel in ('stdout', 'stderr')}}


def test_code(source, cases):
    started = time.monotonic()
    rows = []
    detail = ''
    status = 'missing_tests'
    if cases:
        try:
            if not isinstance(cases, list) or len(cases) > 16 or any(
                    not isinstance(c, dict) or set(c) != {'input', 'expected'} for c in cases):
                raise ValueError('Expected at most 16 input/expected pairs.')
            with Monty(max_processes=1, request_timeout=4) as pool:
                for case in cases:
                    rows.append(run_case(pool, source, case))
            status = next((r['status'] for r in rows if r['status'] != 'passed'), 'passed')
        except (OSError, RuntimeError, ValueError, MontyError) as exc:
            status, detail = 'error', str(exc)[:1024]
    return {'status': status, 'cases': rows, 'detail': detail, 'duration': time.monotonic() - started,
            'exitCode': 0 if status == 'passed' else 1}


def generate(config, model, task):
    prompt = ('Write a pure Python function solve(value) for the following task. Return a JSON object '
              'with a single "source" string. Use builtins only.\n' +
              json.dumps({k: task[k] for k in ('title', 'instructions', 'expectedOutput', 'acceptanceCriteria')}))
    body = {'model': model, 'prompt': prompt, 'stream': False, 'format': 'json',
            'options': {'num_predict': task['maxTokens']}}
    headers = {'Content-Type': 'application/json'}
    if config['ollama_key']:
        headers['Authorization'] = 'Bearer ' + config['ollama_key']
    req = urllib.request.Request(config['ollama_url'].rstrip('/') + '/api/generate',
                                 json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=180) as response:
        source = json.loads(json.loads(response.read(262145))['response'])['source']
    if not isinstance(source, str) or not source.strip() or len(source.encode()) > 32768:
        raise ValueError('Expected a nonempty source string of at most 32 KiB.')
    return source


def execute(config, model, task):
    started = time.monotonic()
    source = ''
    try:
        source = generate(config, model, task)
        result = test_code(source, task['testCases'])
    except (KeyError, TypeError, ValueError, OSError) as exc:
        result = {'status': 'error', 'detail': str(exc)[:1024], 'cases': [], 'exitCode': 1,
                  'duration': time.monotonic() - started}
    report = dict(result, source=source, attempt=task['attempt'])
    report['review'] = review_code(config, model, task, report)
    return report
