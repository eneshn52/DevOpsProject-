from datetime import timedelta

import pytest
from django.utils import timezone

from .models import Agent, Task
from .scheduler import is_eligible, pick_task_for, score

NOW = timezone.now()


def make_agent(**changes):
    """A healthy agent; each test changes only the field it is about."""
    fields = {'pk': 1, 'status': 'IDLE', 'last_heartbeat': NOW - timedelta(seconds=10), 'context_window': 8192}
    fields.update(changes)
    return Agent(**fields)


def make_task(**changes):
    """A task that fits an 8192-token window; tests change only what they are about."""
    fields = {'est_tokens': 2000, 'max_tokens': 2048}
    fields.update(changes)
    return Task(**fields)


# --- is_eligible ---

def test_healthy_agent_is_eligible():
    assert is_eligible(make_agent(), make_task(), NOW, set()) is True


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


# --- score ---

AGENT_A = {'coding': 90, 'reasoning': 85, 'context': 95, 'speed': 50}
AGENT_B = {'coding': 75, 'reasoning': 95, 'context': 70, 'speed': 50}
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
    weights = {'coding': 1, 'reasoning': 1, 'context': 1, 'speed': 1}   # sum is 4, so the division is tested
    assert score(AGENT_A, weights, 0) == pytest.approx(80.0)


def test_score_agent_a_code_review():
    assert score(AGENT_A, CODE_REVIEW, 0) == pytest.approx(88.5)


def test_score_agent_a_zero_weights():
    weights = {'coding': 0.0, 'reasoning': 0.0, 'context': 0.0, 'speed': 0.0}
    with pytest.raises(ValueError):
        score(AGENT_A, weights, 0)


# --- pick_task_for ---

SCORES = {1: AGENT_A, 2: AGENT_B, 3: AGENT_C}
WEIGHTS = {'BACKEND': BACKEND, 'FRONTEND': FRONTEND}


def trio():
    """Agents A, B and C from the brief, all online and idle."""
    return make_agent(pk=1), make_agent(pk=2), make_agent(pk=3)


def pick(asker, tasks, candidates, queued=None, failed=None, scores=SCORES):
    return pick_task_for(asker, tasks, candidates, NOW, scores, queued or {}, failed or {}, WEIGHTS)


def test_agent_skips_task_a_better_agent_should_get():
    a, b, c = trio()
    assert pick(c, [make_task(pk=10, type='BACKEND')], [a, b, c]) is None   # A wins backend 85.5 vs 85


def test_best_agent_gets_the_task():
    a, b, c = trio()
    backend = make_task(pk=10, type='BACKEND')
    assert pick(a, [backend], [a, b, c]) is backend


def test_agent_takes_the_task_it_is_best_at():
    a, b, c = trio()
    backend, frontend = make_task(pk=10, type='BACKEND'), make_task(pk=11, type='FRONTEND')
    assert pick(c, [backend, frontend], [a, b, c]) is frontend   # C loses backend, wins frontend 84 vs 82


def test_queue_penalty_lets_another_agent_win():
    a, b, c = trio()
    backend = make_task(pk=10, type='BACKEND')
    assert pick(c, [backend], [a, b, c], queued={1: 1}) is backend   # A drops to 75.5


def test_agent_that_failed_is_not_a_competitor():
    a, b, c = trio()
    backend = make_task(pk=10, type='BACKEND')
    assert pick(c, [backend], [a, b, c], failed={10: {1}}) is backend


def test_offline_agent_is_not_a_competitor():
    _, b, c = trio()
    a = make_agent(pk=1, last_heartbeat=NOW - timedelta(seconds=60))
    backend = make_task(pk=10, type='BACKEND')
    assert pick(c, [backend], [a, b, c]) is backend


def test_no_ready_tasks_returns_none():
    a, b, c = trio()
    assert pick(a, [], [a, b, c]) is None


def test_agent_without_scores_gets_nothing():
    a, b, c = trio()
    assert pick(a, [make_task(pk=10, type='BACKEND')], [a, b, c], scores={2: AGENT_B, 3: AGENT_C}) is None


def test_tie_goes_to_the_asking_agent():
    first, second = make_agent(pk=1), make_agent(pk=2)
    backend = make_task(pk=10, type='BACKEND')
    assert pick(second, [backend], [first, second], scores={1: AGENT_A, 2: AGENT_A}) is backend


def test_unknown_task_type_uses_default_weights():
    a, b, c = trio()
    general = make_task(pk=10, type='GENERAL')
    assert pick(a, [general], [a, b, c]) is general   # equal weights: A 80, C 76.25, B 72.5