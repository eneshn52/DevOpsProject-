"""FR-10: agent profiles are updated from real task outcomes after each task.

How it works
------------
1. Every finished attempt already leaves a Metric row (FR-16): success or failure,
   time spent, and tokens. Benchmark tasks (FR-9) are left out here because they
   measure the agent in a test, not on real work.
2. After each outcome, refresh_profile(agent) rebuilds the agent's AgentProfile from
   those rows: tasks completed and failed, success rate, average time spent and
   output speed (tokens per second).
3. When the master picks an agent for a task (FR-14), adjusted_scores() blends the
   agent's benchmark scores with what it actually achieved:
     coding and reasoning  <- success rate x 100   (does its work get accepted?)
     speed                 <- real tokens per second, on the same scale as FR-9
     context               <- unchanged            (task outcomes do not measure it)
   The more outcomes an agent has, the more they count, up to MAX_OBSERVED_WEIGHT:
     weight = min(MAX_OBSERVED_WEIGHT, outcomes / (outcomes + TRUST_OUTCOMES))
   So a new agent is judged by its benchmark, and one lucky or unlucky task cannot
   outweigh it. The stored benchmark scores themselves are never overwritten, so a
   new benchmark run (FR-9) still sets the baseline.

Tests: django_modules/test_agent_profiles.py
"""
import statistics

from django.db.models import Count, Max, Q, Sum

from .benchmarking import SPEED_TARGET_TOKENS_PER_SECOND
from .models import AgentProfile, Metric, WorkerTaskResult

RECENT_WINDOW = 50          # rates and averages use the agent's last 50 real outcomes
TRUST_OUTCOMES = 10         # after 10 outcomes, real results count half as much as they can
MAX_OBSERVED_WEIGHT = 0.5   # real results never count for more than half of a score
MIN_SPEED_TOKENS = 32       # same minimum sample size FR-9 uses for a speed measurement

REAL_WORK = Q(task__isnull=True) | Q(task__benchmark_case__isnull=True)


def real_outcomes(agent):
    """The agent's Metric rows from real tasks, newest first."""
    return Metric.objects.filter(REAL_WORK, agent=agent).order_by('-pk')


def tokens_per_second(metrics):
    """Median output speed of successful outcomes with a usable timing, or None."""
    speeds = [m.output_tokens / m.duration_s for m in metrics
              if m.success and m.duration_s > 0 and m.output_tokens >= MIN_SPEED_TOKENS]
    return round(statistics.median(speeds), 2) if speeds else None


def refresh_profile(agent):
    """Rebuild this agent's profile from its real outcomes and return it.

    Safe to call any number of times: it always recalculates from the Metric rows,
    so the profile cannot drift from the data. Call it right after a Metric is saved.
    """
    outcomes = real_outcomes(agent)
    totals = outcomes.aggregate(count=Count('pk'), completed=Count('pk', filter=Q(success=True)),
                                total_time=Sum('duration_s', default=0.0))
    recent = list(outcomes[:RECENT_WINDOW])
    last = (WorkerTaskResult.objects
            .filter(Q(task__benchmark_case__isnull=True), agent=agent, completed_at__isnull=False)
            .aggregate(last=Max('completed_at'))['last'])

    values = {
        'tasks_completed': totals['completed'],
        'tasks_failed': totals['count'] - totals['completed'],
        'sample_size': len(recent),
        'success_rate': round(sum(m.success for m in recent) / len(recent), 4) if recent else None,
        'avg_duration_s': round(sum(m.duration_s for m in recent) / len(recent), 2) if recent else None,
        'tokens_per_second': tokens_per_second(recent),
        'total_time_s': round(totals['total_time'] or 0.0, 2),
        'last_outcome_at': last,
    }
    profile, _ = AgentProfile.objects.update_or_create(agent=agent, defaults=values)
    return profile


def observed_scores(profile):
    """Turn a profile into 0-100 scores for the metrics that real work measures."""
    if profile is None or not profile.sample_size:
        return {}
    observed = {}
    if profile.success_rate is not None:
        observed['coding'] = observed['reasoning'] = round(100.0 * profile.success_rate, 2)
    if profile.tokens_per_second is not None:
        observed['speed'] = round(min(100.0, 100.0 * profile.tokens_per_second / SPEED_TARGET_TOKENS_PER_SECOND), 2)
    return observed


def observed_weight(sample_size):
    """How much real outcomes count (0 to MAX_OBSERVED_WEIGHT); grows with the number of outcomes."""
    if sample_size <= 0:
        return 0.0
    return min(MAX_OBSERVED_WEIGHT, sample_size / (sample_size + TRUST_OUTCOMES))


def blend(benchmark, profile):
    """Mix one agent's benchmark scores with its observed scores; returns a new dict."""
    observed = observed_scores(profile)
    weight = observed_weight(profile.sample_size if profile else 0)
    if not observed or weight == 0:
        return dict(benchmark)
    return {metric: (round((1 - weight) * value + weight * observed[metric], 2) if metric in observed else value)
            for metric, value in benchmark.items()}


def adjusted_scores(scores):
    """Apply each agent's track record to the scheduler's scores.

    scores: {agent_pk: {'coding': .., 'reasoning': .., 'context': .., 'speed': ..}} from
    capabilities.get_scores(). Agents missing from it (not benchmarked) stay missing, so
    FR-14's rule "no benchmark, no tasks" still holds.
    """
    profiles = {p.agent_id: p for p in AgentProfile.objects.filter(agent_id__in=list(scores))}
    return {agent_id: blend(values, profiles.get(agent_id)) for agent_id, values in scores.items()}


def profile_json(agent):
    """The profile as the workspace API shows it, or None before the first real task."""
    try:
        profile = agent.profile
    except AgentProfile.DoesNotExist:
        return None
    return {'tasksCompleted': profile.tasks_completed, 'tasksFailed': profile.tasks_failed,
            'successRate': profile.success_rate, 'avgDurationS': profile.avg_duration_s,
            'tokensPerSecond': profile.tokens_per_second, 'totalTimeS': profile.total_time_s,
            'sampleSize': profile.sample_size,
            'lastOutcomeAt': profile.last_outcome_at.isoformat() if profile.last_outcome_at else None}
