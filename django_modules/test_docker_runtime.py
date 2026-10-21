"""Check readiness responses and container-only model address translation."""
import io
import json
import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings

from .chat_service import call_model, model_endpoint_url


class HealthTests(TestCase):
    def test_readiness_checks_database_without_workspace_access(self):
        """Permit infrastructure readiness probes without exposing private records."""
        response = self.client.get('/health/', REMOTE_ADDR='192.168.1.50')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(self.client.post('/health/').status_code, 405)

    def test_database_failure_returns_only_unavailable(self):
        """Keep database credentials and exception details out of health responses."""
        with patch('django_modules.views.connection.cursor', side_effect=OperationalError('private credentials')):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})


class ModelAddressTests(SimpleTestCase):
    @override_settings(OLLAMA_LOOPBACK_HOST='')
    def test_native_model_urls_remain_unchanged(self):
        """Preserve endpoint behavior when running Python directly on the host."""
        self.assertEqual(model_endpoint_url('http://localhost:11434'), 'http://localhost:11434')

    @override_settings(OLLAMA_LOOPBACK_HOST='host.docker.internal')
    def test_container_resolves_saved_loopback_addresses(self):
        """Translate host loopback URLs while preserving scheme, port, and base path."""
        for host in ['localhost', '127.0.0.1', '[::1]']:
            with self.subTest(host=host):
                self.assertEqual(model_endpoint_url(f'http://{host}:11434/ollama'), 'http://host.docker.internal:11434/ollama')
        for url in ['http://192.168.1.20:11434', 'http://ollama:11434', 'https://worker.example.com']:
            self.assertEqual(model_endpoint_url(url), url)

    @override_settings(OLLAMA_LOOPBACK_HOST='ollama')
    def test_optional_ollama_container_receives_model_calls(self):
        """Resolve existing local model configurations to the optional Compose service."""
        response = io.BytesIO(json.dumps({'message': {'content': 'Ready'}}).encode())
        with patch('django_modules.chat_service.urllib.request.urlopen', return_value=response) as call:
            answer = call_model('test:3b', {'url': 'http://localhost:11434', 'key': 'test-key'}, [{'role': 'user', 'content': 'Hello'}])
        self.assertEqual(answer, 'Ready')
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, 'http://ollama:11434/api/chat')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')


BROWSER_RELOAD_PROBE = """
import importlib.util, json, sys
if sys.argv[1] == 'missing':
    original = importlib.util.find_spec
    importlib.util.find_spec = lambda name, *args: None if name == 'django_browser_reload' else original(name, *args)
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.urls import Resolver404, resolve
call_command('check', stdout=open(__import__('os').devnull, 'w'))
try:
    resolve('/__reload__/events/'); routed = True
except Resolver404:
    routed = False
print(json.dumps({'enabled': settings.BROWSER_RELOAD, 'app': 'django_browser_reload' in settings.INSTALLED_APPS,
                  'middleware': settings.MIDDLEWARE[-1] == 'django_browser_reload.middleware.BrowserReloadMiddleware',
                  'anyMiddleware': any(item.startswith('django_browser_reload.') for item in settings.MIDDLEWARE), 'routed': routed}))
"""


class BrowserReloadSettingsTests(SimpleTestCase):
    def probe(self, settings_module, mode='installed'):
        """Load a settings module in a fresh interpreter and report how browser reload is wired."""
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': settings_module}
        result = subprocess.run([sys.executable, '-c', BROWSER_RELOAD_PROBE, mode], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parent.parent, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    @skipUnless(find_spec('django_browser_reload'), 'django-browser-reload is a development-only dependency')
    def test_debug_development_settings_enable_browser_reload(self):
        """Register the app, final middleware, and reload URL for native DEBUG development."""
        self.assertEqual(self.probe('django_settings.settings'),
                         {'enabled': True, 'app': True, 'middleware': True, 'anyMiddleware': True, 'routed': True})

    def test_container_settings_never_enable_browser_reload(self):
        """Keep browser reload out of the non-DEBUG container configuration."""
        self.assertEqual(self.probe('django_settings.docker'),
                         {'enabled': False, 'app': False, 'middleware': False, 'anyMiddleware': False, 'routed': False})

    def test_missing_package_does_not_break_startup(self):
        """Start and pass system checks when the development package is not installed."""
        self.assertEqual(self.probe('django_settings.settings', 'missing'),
                         {'enabled': False, 'app': False, 'middleware': False, 'anyMiddleware': False, 'routed': False})
