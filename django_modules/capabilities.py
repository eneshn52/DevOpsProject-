"""FR-8: agent capability profiles.

Each agent has four scores from 0 to 100, stored as AgentCapabilityScore rows:
coding, reasoning, context and speed. The benchmark (FR-9) produces them,
save_scores() stores them, and the scheduler (FR-14) reads them with get_scores().
"""
import json
import math
from pathlib import Path

from django.db import transaction

from .models import AgentCapabilityScore, CapabilityMetric

METRICS = ('coding', 'reasoning', 'context', 'speed')

# Key in the benchmark's JSON result files -> our metric name
BENCHMARK_SCORE_KEYS = {f'{metric}_score': metric for metric in METRICS}


def ensure_metrics():
    """Create the four metric rows if they are missing; return {name: CapabilityMetric}."""
    return {name: CapabilityMetric.objects.get_or_create(name=name)[0] for name in METRICS}


def save_scores(agent, scores):
    """Store some or all of an agent's four scores, replacing older values.

    scores: dict like {'coding': 82.5, 'speed': 40}. Raises ValueError for an unknown
    metric or a value that is not a number between 0 and 100; nothing is saved then.
    """
    unknown = set(scores) - set(METRICS)
    if unknown:
        raise ValueError(f'Unknown metrics: {", ".join(sorted(unknown))}. Use {", ".join(METRICS)}.')
    for name, value in scores.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value) or not 0 <= value <= 100:
            raise ValueError(f'{name} must be a number between 0 and 100, got {value!r}.')

    metrics = ensure_metrics()
    with transaction.atomic():
        for name, value in scores.items():
            AgentCapabilityScore.objects.update_or_create(
                agent=agent, metric=metrics[name], defaults={'score': float(value)})


def get_scores(agent_ids):
    """Return {agent_pk: {'coding': .., 'reasoning': .., 'context': .., 'speed': ..}}.

    Only agents with all four scores are included. An agent that is missing any
    score has not been fully benchmarked, so the scheduler gives it no tasks.
    """
    rows = (AgentCapabilityScore.objects
            .filter(agent_id__in=agent_ids, metric__name__in=METRICS)
            .values_list('agent_id', 'metric__name', 'score'))
    profiles = {}
    for agent_id, name, value in rows:
        profiles.setdefault(agent_id, {})[name] = value
    return {agent_id: scores for agent_id, scores in profiles.items() if len(scores) == len(METRICS)}


def read_benchmark_results(folder, model):
    """Find the newest valid benchmark score for each metric in a folder of result files.

    Reads the JSON files the FR-9 benchmark writes (benchmark/<model>/*.json).
    A file counts only if it is for `model`, finished ('status': 'completed') and
    marked 'score_valid': true. Returns {metric: (score, path)}.
    """
    newest = {}
    for path in sorted(Path(folder).rglob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue  # unreadable or half-written file: skip it
        if not isinstance(data, dict) or data.get('model') != model:
            continue
        if data.get('status') != 'completed' or data.get('score_valid') is not True:
            continue
        stamp = str(data.get('updated_at', ''))
        for key, metric in BENCHMARK_SCORE_KEYS.items():
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if metric not in newest or stamp > newest[metric][2]:
                    newest[metric] = (float(value), path, stamp)
    return {metric: (value, path) for metric, (value, path, _) in newest.items()}
