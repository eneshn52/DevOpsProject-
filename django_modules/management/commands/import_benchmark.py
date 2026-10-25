"""Load benchmark scores (FR-9 result files) into an agent's capability profile (FR-8).

Usage:
    python manage.py import_benchmark AGENT_ID [RESULTS_FOLDER] [--model MODEL_NAME]

RESULTS_FOLDER defaults to the repo's benchmark/ folder. The model defaults to the
agent's own model (Agent.ai_model). For each of coding, reasoning, context and
speed, the newest finished and valid result file is used.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from django_modules.capabilities import METRICS, read_benchmark_results, save_scores
from django_modules.models import Agent


class Command(BaseCommand):
    help = "Load the newest valid benchmark scores into an agent's capability profile."

    def add_arguments(self, parser):
        parser.add_argument('agent_id', type=int)
        parser.add_argument('results_folder', nargs='?', default=str(settings.BASE_DIR / 'benchmark'))
        parser.add_argument('--model', help="Model name used in the result files (default: the agent's model)")

    def handle(self, *args, **options):
        agent = Agent.objects.exclude(role='MASTER').select_related('ai_model').filter(pk=options['agent_id']).first()
        if agent is None:
            raise CommandError(f"No specialist agent with id {options['agent_id']}.")
        model = options['model'] or (agent.ai_model.ollama_name if agent.ai_model else None)
        if not model:
            raise CommandError('This agent has no model set. Pass --model with the name used in the result files.')

        results = read_benchmark_results(options['results_folder'], model)
        if not results:
            raise CommandError(f"No finished, valid results for {model!r} in {options['results_folder']}.")

        save_scores(agent, {metric: value for metric, (value, _) in results.items()})
        for metric in METRICS:
            if metric in results:
                value, path = results[metric]
                self.stdout.write(f'{metric:<10} {value:6.2f}   from {path.name}')
            else:
                self.stdout.write(f'{metric:<10} missing  (agent gets no tasks until all four are known)')
        self.stdout.write(self.style.SUCCESS(f'Saved {len(results)} score(s) for {agent.name}.'))
