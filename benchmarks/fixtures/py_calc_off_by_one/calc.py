"""A tiny calculator module with a planted off-by-one bug.

The benchmark expects the agent to find and fix the bug in `add`.
"""


def add(a, b):
    # BUG: planted off-by-one — should be `a + b`.
    return a + b + 1
