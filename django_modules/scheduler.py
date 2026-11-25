"""FR-14: agent selection. Pure functions first; Django wiring comes later.

Goes in django_modules/scheduler.py. Tests: django_modules/test_scheduler.py.
Functions take Agent/Task objects but run no queries, so tests can use
unsaved instances, e.g. Agent(pk=1, status='IDLE', last_heartbeat=..., context_window=8192).
"""

from .task_weights import DEFAULT_WEIGHTS

# --- Constants (tune later, keep them here so they are easy to find) ---
# The heartbeat timeout lives in settings.AGENT_HEARTBEAT_TIMEOUT (FR-6); reuse Agent.is_online().
CONTEXT_SAFETY = 0.8         # use at most 80% of the context window
DEFAULT_EST_TOKENS = 2000    # the planner does not fill est_tokens yet
DEFAULT_MAX_TOKENS = 2048    # the planner does not fill max_tokens yet
QUEUE_PENALTY = 10           # λ: points subtracted per task already queued on the agent


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
    if not agent.is_online(now):
        return False

    # Rule 2: does the task fit the context window?
    if agent.context_window is None:
        return False
    est = task.est_tokens if task.est_tokens is not None else DEFAULT_EST_TOKENS
    out = task.max_tokens if task.max_tokens is not None else DEFAULT_MAX_TOKENS
    if est + out > CONTEXT_SAFETY * agent.context_window:
        return False

    # Rule 3: has this agent already failed this task?
    if agent.pk in failed_agent_ids:
        return False

    return True


def score(agent_scores, weights, queued):
    """Return S(a, t): the weighted average of the agent's scores minus a queue penalty.

    agent_scores: dict like {'coding': 90, 'reasoning': 85, 'context': 95, 'speed': 50}
    weights:      dict with the same four keys, e.g. backend {'coding': 0.6, 'reasoning': 0.2, 'context': 0.1, 'speed': 0.1}
    queued:       number of tasks already assigned to this agent (Q)

    S = sum(weight * score for each metric) / sum(weights)  -  QUEUE_PENALTY * queued
    A metric missing from agent_scores counts as 0.
    If the weights do not add up to a positive number, raise ValueError (a broken weight table should fail loudly).
    """
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("Weights must sum to a positive number.")

    weighted_sum = sum(weight * agent_scores.get(metric, 0) for metric, weight in weights.items())

    return weighted_sum / total_weight - QUEUE_PENALTY * queued


def pick_task_for(agent, ready_tasks, candidates, now, scores, queued, failed, weights):
    """Return the task the asking agent should take now, or None.

    agent:       the Agent asking for work
    ready_tasks: list of ready Tasks, oldest first (the caller sorts them)
    candidates:  list of all specialist Agents, including the asking one
    now:         the master's current time
    scores:      {agent_pk: {'coding': .., 'reasoning': .., 'context': .., 'speed': ..}}
    queued:      {agent_pk: number of tasks already assigned}   (missing = 0)
    failed:      {task_pk: set of agent pks that failed it}      (missing = empty set)
    weights:     {task_type: {'coding': .., 'reasoning': .., 'context': .., 'speed': ..}}

    Rules:
      - An agent with no entry in `scores` (not benchmarked yet) never takes a task.
      - For each task, oldest first:
          * if the asking agent is not eligible, skip the task;
          * score every eligible candidate (eligible + has scores);
          * if the asking agent has the highest score, return this task;
          * otherwise move on to the next task (a better agent will ask for it).
      - On a tie for the highest score, the asking agent wins (it is idle and here now).
      - Stored task capability weights take precedence; unclassified legacy/unsaved tasks use
        the supplied type table, then DEFAULT_WEIGHTS for types missing from the table.
      - No task won: return None.

    This function only decides; it does not claim or save anything.
    """
    # An agent that has not been benchmarked cannot take anything
    if agent.pk not in scores:
        return None

    for task in ready_tasks:
        failed_agents = failed.get(task.pk, set())
        if not is_eligible(agent, task, now, failed_agents):
            continue

        task_weights = task.capability_weights or weights.get(task.type, DEFAULT_WEIGHTS)
        asking_agent_score = None
        highest_score = float('-inf')

        for candidate in candidates:
            if candidate.pk not in scores:
                continue
            if not is_eligible(candidate, task, now, failed_agents):
                continue

            candidate_score = score(scores[candidate.pk], task_weights, queued.get(candidate.pk, 0))

            if candidate.pk == agent.pk:
                asking_agent_score = candidate_score
            if candidate_score > highest_score:
                highest_score = candidate_score

        # The asking agent's own score is part of highest_score, so >= means "best, or tied for best"
        if asking_agent_score is not None and asking_agent_score >= highest_score:
            return task

    return None
