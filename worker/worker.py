"""Specialist worker: report liveness to the workspace with a heartbeat every 10 seconds (FR-6).

The worker identifies as an existing specialist (by id or name), checks that its local Ollama
endpoint is reachable and has the specialist's model, and posts to
POST /api/agents/{id}/heartbeat. Ctrl+C stops the worker; the workspace then shows the agent
Offline once 30 seconds pass without a heartbeat (or immediately with --sign-off).

Standard library only, so it runs on any computer with Python 3.10+.
Usage: python worker/worker.py --config worker/config.json
"""
import argparse
import http.cookiejar
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULTS = {'server_url': 'http://127.0.0.1:8000', 'agent_id': None, 'agent_name': '', 'ollama_url': 'http://127.0.0.1:11434',
            'ollama_key': '', 'model': '', 'interval': 10, 'timeout': 5, 'username': '', 'password': ''}
logger = logging.getLogger('worker')


class WorkerError(Exception):
    """A problem the worker cannot recover from by retrying."""


class Workspace:
    """Minimal client for the workspace API using a cookie-aware opener."""

    def __init__(self, base_url, timeout):
        """Prepare a session that keeps the Django login cookie when one is used."""
        self.base_url, self.timeout = base_url.rstrip('/'), timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method, path, data=None, form=False):
        """Send a request and return the decoded JSON body, raising HTTPError on failures."""
        headers = {'Accept': 'application/json'}
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        if form:
            headers['Referer'] = self.base_url + path
            token = next((cookie.value for cookie in self.cookies if cookie.name == 'csrftoken'), '')
            headers['X-CSRFToken'] = token
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read()
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def login(self, username, password):
        """Sign in as a staff user; required when the worker is not on the server's computer."""
        self.request('GET', '/admin/login/')
        token = next((cookie.value for cookie in self.cookies if cookie.name == 'csrftoken'), '')
        self.request('POST', '/admin/login/', {'csrfmiddlewaretoken': token, 'username': username, 'password': password,
                                               'next': '/admin/'}, form=True)
        if not any(cookie.name == 'sessionid' for cookie in self.cookies):
            raise WorkerError('Sign-in failed: check username/password and that the user is staff.')

    def find_agent(self, agent_id, agent_name):
        """Return the specialist record that matches the configured id or name."""
        agents = self.request('GET', '/api/agents/')['data']
        if agent_id is not None:
            match = [item for item in agents if item['id'] == str(agent_id)]
        else:
            match = [item for item in agents if item['name'].strip().lower() == agent_name.strip().lower()]
        if len(match) != 1:
            names = ', '.join(f"{item['name']} (id {item['id']})" for item in agents) or 'none'
            wanted = f'id {agent_id}' if agent_id is not None else f'name "{agent_name}"'
            reason = 'several specialists share' if match else 'no specialist has'
            raise WorkerError(f'{reason.capitalize()} {wanted}. Registered specialists: {names}.')
        return match[0]

    def heartbeat(self, agent_id, alive=True):
        """Post one heartbeat; the endpoint needs no CSRF token."""
        data = {'alive': True, 'status': 'IDLE'} if alive else {'alive': False}
        return self.request('POST', f'/api/agents/{agent_id}/heartbeat/', data)['data']


def ollama_problem(url, model, key, timeout):
    """Return None when Ollama answers and has the model, otherwise a short reason."""
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url.rstrip('/') + '/api/tags', headers=headers), timeout=timeout) as response:
            names = {item.get('name') for item in json.load(response).get('models', [])}
    except (OSError, ValueError) as error:
        return f'Ollama at {url} is not reachable ({getattr(error, "reason", error)})'
    wanted = model if ':' in model else f'{model}:latest'
    if model and wanted not in names:
        return f'Ollama at {url} does not have model "{model}" (run: ollama pull {model})'
    return None


def load_config(args):
    """Merge defaults, the JSON config file, environment secrets, and command-line overrides."""
    config = dict(DEFAULTS)
    if args.config:
        path = Path(args.config)
        if not path.is_file():
            raise WorkerError(f'Config file not found: {path}')
        loaded = json.loads(path.read_text(encoding='utf-8'))
        unknown = set(loaded) - set(DEFAULTS)
        if unknown:
            raise WorkerError(f'Unknown config keys: {", ".join(sorted(unknown))}')
        config.update(loaded)
    config['username'] = os.getenv('WORKER_USERNAME', config['username'])
    config['password'] = os.getenv('WORKER_PASSWORD', config['password'])
    for key in ('server_url', 'agent_id', 'agent_name', 'ollama_url', 'model', 'interval'):
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    if config['agent_id'] == '':
        config['agent_id'] = None
    if config['agent_id'] is None and not config['agent_name']:
        raise WorkerError('Set agent_id or agent_name in the config or on the command line.')
    if not 1 <= float(config['interval']) <= 25:
        raise WorkerError('interval must be between 1 and 25 seconds so the agent stays online (timeout is 30 s).')
    return config


def http_reason(error):
    """Describe a workspace HTTP failure using the API's error body when available."""
    try:
        detail = json.loads(error.read()).get('error', '')
    except (ValueError, AttributeError):
        detail = ''
    hint = ' Set username/password (a staff user) when the worker runs on another computer.' if error.code == 403 else ''
    return f'HTTP {error.code} {detail}{hint}'.strip()


def wait(seconds):
    """Sleep in short steps so Ctrl+C is handled promptly on every platform."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(min(0.25, end - time.monotonic()))


def run(config, once=False, sign_off=False):
    """Resolve the agent, then send a heartbeat whenever the local model endpoint is ready."""
    workspace = Workspace(config['server_url'], float(config['timeout']))
    if config['username']:
        workspace.login(config['username'], config['password'])
        logger.info('Signed in to %s as %s', config['server_url'], config['username'])
    agent = workspace.find_agent(config['agent_id'], config['agent_name'])
    model = config['model'] or agent.get('modelName', '')
    logger.info('Worker for specialist "%s" (id %s, model %s) -> %s', agent['name'], agent['id'], model or 'any', config['server_url'])
    ready = None
    try:
        while True:
            problem = ollama_problem(config['ollama_url'], model, config['ollama_key'], float(config['timeout']))
            try:
                if problem is None:
                    result = workspace.heartbeat(agent['id'])
                    if ready is not True:
                        logger.info('Ollama ready; heartbeat accepted, agent is %s. Sending every %ss.',
                                    'online' if result.get('online') else result.get('status'), config['interval'])
                    logger.debug('Heartbeat sent')
                    ready = True
                elif ready is not False:
                    logger.warning('%s. Pausing heartbeats until it is back; the agent shows Offline after 30 s.', problem)
                    ready = False
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    raise WorkerError(f'Specialist id {agent["id"]} no longer exists.') from None
                logger.warning('Heartbeat rejected: %s', http_reason(error))
            except OSError as error:
                logger.warning('Workspace %s is not reachable (%s); retrying.', config['server_url'], getattr(error, 'reason', error))
            if once:
                return 0 if ready else 1
            wait(float(config['interval']))
    except KeyboardInterrupt:
        if not sign_off:
            logger.info('Stopped. The agent shows Offline once 30 s pass without a heartbeat.')
            return 0
        try:
            workspace.heartbeat(agent['id'], alive=False)
            logger.info('Stopped and signed off; the agent shows Offline now.')
        except OSError as error:
            logger.warning('Could not sign off (%s); the agent shows Offline after 30 s.', error)
        return 0


def stop(*_):
    """Treat a termination signal like Ctrl+C so the worker stops the same way."""
    raise KeyboardInterrupt


def main(argv=None):
    """Parse arguments, configure logging, and run until interrupted."""
    parser = argparse.ArgumentParser(description='Send specialist heartbeats to the workspace.')
    parser.add_argument('--config', help='Path to a JSON config (see worker/config.example.json).')
    parser.add_argument('--server-url', dest='server_url')
    parser.add_argument('--agent-id', dest='agent_id', type=int)
    parser.add_argument('--agent-name', dest='agent_name')
    parser.add_argument('--ollama-url', dest='ollama_url')
    parser.add_argument('--model', help='Override the model to check (default: the specialist\'s configured model).')
    parser.add_argument('--interval', type=float)
    parser.add_argument('--once', action='store_true', help='Send a single heartbeat and exit.')
    parser.add_argument('--sign-off', action='store_true', help='On Ctrl+C, mark the agent offline immediately instead of letting it time out.')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, stop)  # Ctrl+Break on Windows
    try:
        return run(load_config(args), once=args.once, sign_off=args.sign_off)
    except WorkerError as error:
        logger.error('%s', error)
        return 2
    except urllib.error.HTTPError as error:
        logger.error('Workspace request failed: %s', http_reason(error))
        return 2
    except OSError as error:
        logger.error('The workspace server is not reachable: %s', getattr(error, 'reason', error))
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
