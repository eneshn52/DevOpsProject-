"""Check readiness responses and container-only model address translation."""
import io
import json
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
