"""Submit a sample task to the isolated FR-21 development master."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from django_modules.capabilities import save_scores
from django_modules.models import Agent, Task


class Command(BaseCommand):
    help = 'Register the local demo worker and enqueue a Python task.'

    def add_arguments(self, parser):
        parser.add_argument('--cases', type=Path, default=Path('worker/example_cases.json'))

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.SETTINGS_MODULE != 'django_settings.fr21_local':
            raise CommandError('Use --settings=django_settings.fr21_local for the demo.')
        path = options['cases']
        try:
            cases = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            self.stderr.write(f'{path} is missing. The task will report missing_tests.')
            cases = []
        if not isinstance(cases, list):
            raise CommandError('The case file must contain a JSON array.')
        agent, _ = Agent.objects.get_or_create(name='fr21-local', defaults={'context_window': 8192})
        save_scores(agent, dict(coding=80, reasoning=80, context=80, speed=80))
        task = Task.objects.create(title='Double an integer', instructions='Return twice the input integer.',
                                   expected_output='Return one integer directly from solve(value).',
                                   test_cases=cases, max_tokens=512)
        self.stdout.write(f'/api/agents/{agent.pk}/tasks/{task.pk}/test-result/')
