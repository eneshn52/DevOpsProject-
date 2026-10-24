from datetime import timedelta

from django.utils import timezone

from .models import Agent, Task
from .scheduler import is_eligible

NOW = timezone.now()


def make_agent(**changes):
    """A healthy agent; each test changes only the field it is about."""
    fields = {'pk': 1, 'status': 'IDLE', 'last_heartbeat': NOW - timedelta(seconds=10), 'context_window': 8192}
    fields.update(changes)
    return Agent(**fields)

def test_healthy_agent_is_eligible():
    # 1. Make an agent with make_agent() (no changes needed)
    # 2. Make a Task with est_tokens=2000 and max_tokens=2048
    # 3. Assert that is_eligible(agent, task, NOW, set()) is True
    test_agent = make_agent()
    test_task = Task(est_tokens=2000, max_tokens=2048)
    assert is_eligible(test_agent, test_task, NOW, set()) is True