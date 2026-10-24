from datetime import timedelta

from django.utils import timezone

from .models import Agent, Task
import pytest
from .scheduler import is_eligible, score

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

def make_task(**changes):
    """A task that fits an 8192-token window; tests change only what they are about."""
    fields = {'est_tokens': 2000, 'max_tokens': 2048}
    fields.update(changes)
    return Task(**fields)


def test_heartbeat_35s_ago_not_eligible():
    agent = make_agent(last_heartbeat=NOW - timedelta(seconds=35))
    assert is_eligible(agent, make_task(), NOW, set()) is False


def test_heartbeat_exactly_30s_not_eligible():
    agent = make_agent(last_heartbeat=NOW - timedelta(seconds=30))
    assert is_eligible(agent, make_task(), NOW, set()) is False


def test_no_heartbeat_not_eligible():
    assert is_eligible(make_agent(last_heartbeat=None), make_task(), NOW, set()) is False


def test_signed_off_agent_not_eligible():
    agent = make_agent(status='OFFLINE', last_heartbeat=NOW - timedelta(seconds=1))
    assert is_eligible(agent, make_task(), NOW, set()) is False


def test_task_too_big_not_eligible():
    assert is_eligible(make_agent(), make_task(est_tokens=4000, max_tokens=3000), NOW, set()) is False


def test_missing_token_estimates_use_defaults():
    assert is_eligible(make_agent(), Task(), NOW, set()) is True


def test_unknown_context_window_not_eligible():
    assert is_eligible(make_agent(context_window=None), make_task(), NOW, set()) is False


def test_agent_that_failed_before_not_eligible():
    assert is_eligible(make_agent(), make_task(), NOW, {1}) is False

AGENT_A = {'coding': 90, 'reasoning': 85, 'context': 95, 'speed': 50}
AGENT_C = {'coding': 95, 'reasoning': 70, 'context': 80, 'speed': 60}
BACKEND = {'coding': 0.6, 'reasoning': 0.2, 'context': 0.1, 'speed': 0.1}
FRONTEND = {'coding': 0.6, 'reasoning': 0.1, 'context': 0.1, 'speed': 0.2}
CODE_REVIEW = {'coding': 0.3, 'reasoning': 0.5, 'context': 0.2, 'speed': 0.0}

def test_score_agent_a_backend():
    assert score(AGENT_A, BACKEND, 0) == pytest.approx(85.5)

def test_score_agent_c_frontend():
    assert score(AGENT_C, FRONTEND, 0) == pytest.approx(84.0)

def test_score_agent_a_backend_with_queue():
    assert score(AGENT_A, BACKEND, 1) == pytest.approx(75.5)

def test_score_agent_a_equal_weights():
    weights = {'coding': 1, 'reasoning': 1, 'context': 1, 'speed': 1}
    assert score(AGENT_A, weights, 0) == pytest.approx(80.0)

def test_score_agent_a_code_review():
    assert score(AGENT_A, CODE_REVIEW, 0) == pytest.approx(88.5)

def test_score_agent_a_zero_weights():
    weights = {'coding': 0.0, 'reasoning': 0.0, 'context': 0.0, 'speed': 0.0}
    with pytest.raises(ValueError):
        score(AGENT_A, weights, 0)