"""FR-14: agent selection. Pure functions first; Django wiring comes later.

Goes in django_modules/scheduler.py. Tests: django_modules/test_scheduler.py.
Functions take Agent/Task objects but run no queries, so tests can use
unsaved instances, e.g. Agent(pk=1, status='IDLE', last_heartbeat=..., context_window=8192).
"""

# --- Constants (tune later, keep them here so they are easy to find) ---
# The heartbeat timeout lives in settings.AGENT_HEARTBEAT_TIMEOUT (FR-6); reuse Agent.is_online().
CONTEXT_SAFETY = 0.8                       # use at most 80% of the context window
DEFAULT_EST_TOKENS = 2000                  # the planner does not fill est_tokens yet
DEFAULT_MAX_TOKENS = 2048                  # the planner does not fill max_tokens yet


def is_eligible(agent, task, now, failed_agent_ids):
    """Return True if this agent may take this task.

    agent:            an Agent (uses .pk, .context_window and .is_online(now))
    task:             a Task (uses .est_tokens and .max_tokens, both may be None)
    now:              the master's current time (pass it in; never call timezone.now() here)
    failed_agent_ids: set of agent pks that already failed this task

    Three rules, all must hold:
      1. Online:   agent.is_online(now) is True (FR-6's rule: status not OFFLINE and
                   heartbeat strictly newer than the timeout).
      2. Fits:     est_tokens + max_tokens <= CONTEXT_SAFETY * context_window.
                   None token values fall back to the DEFAULT_ constants.
                   Unknown context_window (None) -> not eligible.
      3. Not failed before: agent.pk not in failed_agent_ids.
    """
    # Rule 1: online?
    if agent is None or not agent.is_online(now):
        return False

    # Rule 2: does the task fit the context window?

    if agent.context_window is None:
        return False
    est = task.est_tokens if task.est_tokens is not None else DEFAULT_EST_TOKENS
    out = task.max_tokens if task.max_tokens is not None else DEFAULT_MAX_TOKENS
    if est + out > CONTEXT_SAFETY * agent.context_window:
        return False

    # Rule 3: has this agent already failed this task?

    if agent is None or agent.pk in failed_agent_ids:
        return False
    return True

QUEUE_PENALTY = 10  # λ: points subtracted per task already queued on the agent

def score(agent_scores, weights, queued):
    """Return S(a, t): the weighted average of the agent's scores minus a queue penalty.

    agent_scores: dict like {'coding': 90, 'reasoning': 85, 'context': 95, 'speed': 50}
    weights:      dict with the same four keys, e.g. backend {'coding': 0.6, 'reasoning': 0.2, 'context': 0.1, 'speed': 0.1}
    queued:       number of tasks already assigned to this agent (Q)

    S = sum(weight * score for each metric) / sum(weights)  -  QUEUE_PENALTY * queued
    A metric missing from agent_scores counts as 0.
    If the weights add up to 0, raise ValueError (a broken weight table should fail loudly).
    """
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("Weights must sum to a positive number.")

    weighted_sum = sum(weight * agent_scores.get(metric, 0) for metric, weight in weights.items())

    return weighted_sum / total_weight - QUEUE_PENALTY * queued

def pick_task_for(agent, ready_tasks, online_agents, now):
    """Return the task this agent should take, or None. (Third function.)"""
    raise NotImplementedError