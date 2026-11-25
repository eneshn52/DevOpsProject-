"""FR-10: rebuild every specialist's profile from the stored task outcomes.

Profiles update by themselves after each task. Run this once after installing FR-10,
so outcomes recorded before it existed are counted too:

    python manage.py rebuild_agent_profiles
"""
from django.core.management.base import BaseCommand

from django_modules.agent_profiles import refresh_profile
from django_modules.models import Agent


class Command(BaseCommand):
    help = "Rebuild each specialist's FR-10 profile (success rate, time spent) from its task outcomes."

    def handle(self, *args, **options):
        agents = Agent.objects.exclude(role='MASTER').order_by('pk')
        for agent in agents:
            profile = refresh_profile(agent)
            rate = '-' if profile.success_rate is None else f'{profile.success_rate:.0%}'
            self.stdout.write(f'{agent.name}: {profile.tasks_completed} completed, '
                              f'{profile.tasks_failed} failed, recent success {rate}')
        self.stdout.write(self.style.SUCCESS(f'Rebuilt {agents.count()} profile(s).'))
