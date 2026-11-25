"""FR-13: shared task types and their required capability weights for FR-14."""

DEFAULT_WEIGHTS = {'coding': 0.25, 'reasoning': 0.25, 'context': 0.25, 'speed': 0.25}

TASK_WEIGHTS = {
    'GENERAL': DEFAULT_WEIGHTS.copy(),
    #                coding        reasoning         context          speed
    'REQUIREMENTS':  {'coding': 0.1, 'reasoning': 0.5, 'context': 0.3, 'speed': 0.1},
    'DATABASE':      {'coding': 0.4, 'reasoning': 0.4, 'context': 0.1, 'speed': 0.1},
    'BACKEND':       {'coding': 0.6, 'reasoning': 0.2, 'context': 0.1, 'speed': 0.1},
    'FRONTEND':      {'coding': 0.6, 'reasoning': 0.1, 'context': 0.1, 'speed': 0.2},
    'TESTING':       {'coding': 0.4, 'reasoning': 0.4, 'context': 0.1, 'speed': 0.1},
    'REVIEW':        {'coding': 0.3, 'reasoning': 0.5, 'context': 0.2, 'speed': 0.0},
    'INTEGRATION':   {'coding': 0.4, 'reasoning': 0.3, 'context': 0.3, 'speed': 0.0},
    'DOCUMENTATION': {'coding': 0.1, 'reasoning': 0.3, 'context': 0.2, 'speed': 0.4},
}


def capability_weights_for(task_type):
    """Copy the type's weights, retaining equal-weight fallback for legacy unknown types."""
    return TASK_WEIGHTS.get(task_type, DEFAULT_WEIGHTS).copy()
