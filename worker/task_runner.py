"""FR-16 worker: python worker/worker.py --execute --config worker/config.json.

Uses the original heartbeat and task distributor through the FR-16 token
adapter. Does not execute generated code, run benchmarks or perform Git actions.
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time
import urllib.error
import urllib.request

try:
    from .worker import Workspace, WorkerError, ollama_problem, http_reason
except ImportError:
    from worker import Workspace, WorkerError, ollama_problem, http_reason

logger = logging.getLogger('worker.execute')


def generate(job, config):
    started = time.monotonic()
    result = {'attempt': job['attempt'], 'lease': job['lease'], 'status': 'FAILED',
              'output': '', 'error': '', 'duration_s': 0, 'prompt_tokens': 0, 'output_tokens': 0}
    try:
        unsigned = {key: value for key, value in job.items() if key != 'signature'}
        signature = hmac.new(config['shared_token'].encode(), json.dumps(unsigned, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()
        if not config['shared_token'] or not hmac.compare_digest(signature, str(job.get('signature', ''))):
            raise ValueError('Master task signature is invalid.')
        if len(job['prompt'].encode()) + 256 > int(job['context_window'] * .8):
            raise ValueError('Input exceeds conservative context budget.')
        headers = {'Content-Type': 'application/json'}
        if config['ollama_key']:
            headers['Authorization'] = 'Bearer ' + config['ollama_key']
        with urllib.request.urlopen(urllib.request.Request(config['ollama_url'].rstrip('/') + '/api/tags', headers=headers), timeout=min(10, job['timeout_s'])) as tags_response:
            tags = json.load(tags_response).get('models', [])
        name = job['model'] if ':' in job['model'] else job['model'] + ':latest'
        info = next((item for item in tags if item.get('name') == name), {})
        if not info.get('digest'):
            raise ValueError('Cannot record the installed model version digest.')
        result['model_metadata'] = {'name': name, 'digest': info['digest'], 'details': info.get('details', {}), 'modified_at': info.get('modified_at', '')}
        body = dict(model=job['model'], prompt=job['prompt'], options=job['options'], stream=False)
        remaining = job['timeout_s'] - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError('Task timeout during model metadata lookup.')
        request = urllib.request.Request(config['ollama_url'].rstrip('/') + '/api/generate',
                                         data=json.dumps(body).encode(), headers=headers, method='POST')
        with urllib.request.urlopen(request, timeout=remaining) as response:
            raw = response.read(2000001)
        if len(raw) > 2000000:
            raise ValueError('Model response exceeds 2 MB.')
        answer = json.loads(raw)
        if not isinstance(answer, dict) or answer.get('done') is not True:
            raise ValueError('Incomplete model response.')
        output = answer.get('response')
        if not isinstance(output, str) or not output.strip() or len(output) > 100000:
            raise ValueError('Model returned no final answer or output was too large.')
        for name in ('prompt_eval_count', 'eval_count'):
            if type(answer.get(name, 0)) is not int or not 0 <= answer.get(name, 0) <= 10000000:
                raise ValueError('Invalid model token metadata.')
        result.update(status='COMPLETED', output=output, prompt_tokens=answer.get('prompt_eval_count', 0),
                      output_tokens=answer.get('eval_count', 0))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['error'] = f'{type(error).__name__}: {error}'[:1000]
    result['duration_s'] = time.monotonic() - started
    if result['duration_s'] > job['timeout_s']:
        result.update(status='FAILED', output='', error='Task execution timeout exceeded.')
    return result


def save_pending(path, task_id, result):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'task_id': task_id, 'result': result}), encoding='utf-8')
    os.replace(temporary, path)


def deliver(workspace, agent_id, path):
    pending = json.loads(path.read_text(encoding='utf-8'))
    try:
        response = workspace.request('POST', f'/api/agents/{agent_id}/tasks/{pending["task_id"]}/result/', pending['result'])
    except urllib.error.HTTPError as error:
        if error.code == 409:
            rejected = path.with_name(path.name + '.' + pending['result']['lease'] + '.rejected')
            os.replace(path, rejected)
        raise
    path.unlink()
    logger.info('Task %s result saved: %s', pending['task_id'], response['data']['status'])
    return response['data']['status']


def run(config, outbox, once=False, sign_off=False):
    workspace = Workspace(config['server_url'], float(config['timeout']))
    workspace.worker_token = config['shared_token']
    if not config['agent_id'] or not isinstance(config['shared_token'], str) or not config['shared_token'] or config['shared_token'] == 'REPLACE_WITH_RANDOM_SHARED_TOKEN':
        raise WorkerError('FR-16 requires agent_id and shared_token (or FR16_WORKER_TOKEN).')
    if config['model']:
        raise WorkerError('Execution uses the agent model configured on the master; leave model empty.')
    agent = workspace.request('GET', f'/api/agents/{config["agent_id"]}/execution-profile/')['data']
    agent_id = agent['id']
    logger.info('FR-16 worker %s -> %s', agent_id, config['server_url'])
    completed = queue.Queue(maxsize=1)
    active = None
    try:
        while True:
            try:
                if active and not completed.empty():
                    save_pending(outbox, active['task_id'], completed.get())
                    active = None
                if outbox.exists():
                    final_status = deliver(workspace, agent_id, outbox)
                    if once:
                        return 0 if final_status == 'COMPLETED' else 1
                if active:
                    workspace.request('POST', f'/api/agents/{agent_id}/heartbeat/',
                        {'alive': True, 'status': 'BUSY', 'currentTaskId': active['task_id']})
                else:
                    problem = ollama_problem(config['ollama_url'], agent.get('modelName', ''),
                                             config['ollama_key'], float(config['timeout']))
                    if problem:
                        logger.warning('%s; no task claimed.', problem)
                        if once:
                            return 1
                    else:
                        workspace.heartbeat(agent_id)
                        task = workspace.request('GET', f'/api/agents/{agent_id}/next-task/').get('data')
                        if task:
                            active = workspace.request('POST', f'/api/agents/{agent_id}/tasks/{task["id"]}/start/', {})['data']
                            job = active
                            thread = threading.Thread(target=lambda: completed.put(generate(job, config)), daemon=True)
                            thread.start()
                            logger.info('Executing task %s with %s', active['task_id'], active['model'])
                        else:
                            logger.info('No task available.')
                            if once:
                                return 0
            except urllib.error.HTTPError as error:
                logger.warning('Master rejected request: %s', http_reason(error))
                if once:
                    return 1
            except (OSError, ValueError) as error:
                logger.warning('Communication failed; pending result retained: %s', error)
                if once:
                    return 1
            time.sleep(float(config['interval']))
    except KeyboardInterrupt:
        if sign_off:
            try:
                workspace.heartbeat(agent_id, alive=False)
            except OSError:
                logger.warning('Sign-off could not be sent; heartbeat expiry still applies.')
        logger.info('Worker stopped. An unfinished lease will expire; restart to replay a pending result.')
        return 0

