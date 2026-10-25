"""How much each skill matters for each task type (used by the FR-14 scheduler).

TEMPORARY: copied from the architecture document's weight table until FR-13
(task classification) delivers the real one. Keys are task types as the planner
writes them in Task.type. Types missing here (for example GENERAL) get equal
weights through scheduler.DEFAULT_WEIGHTS.
"""

TASK_WEIGHTS = {
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
