"""Mark specialists offline when they stop sending heartbeats (FR-6)."""
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from django_modules.models import Agent


class Command(BaseCommand):
    help = 'Mark specialists offline after AGENT_HEARTBEAT_TIMEOUT seconds without a heartbeat.'

    def add_arguments(self, parser):
        """Allow a one-off sweep or a repeating watch loop."""
        parser.add_argument('--watch', action='store_true', help='Repeat the sweep every AGENT_HEARTBEAT_INTERVAL seconds.')

    def handle(self, *args, **options):
        """Run the freshness sweep once, or repeatedly with --watch."""
        while True:
            count = Agent.mark_stale()
            self.stdout.write(f'Marked {count} agent(s) offline.')
            if not options['watch']:
                return
            time.sleep(settings.AGENT_HEARTBEAT_INTERVAL)
