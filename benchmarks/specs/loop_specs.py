"""Loop-mode benchmarks: long-horizon iterative work."""

from ..rubrics import (
    CommandSucceeds,
    FileContains,
    FileRegex,
    MaxToolCalls,
    ResponseContains,
)
from ..spec import BenchmarkSpec


SPECS = [
    BenchmarkSpec(
        name="loop__iterative_calc_polish",
        mode="loop",
        description=(
            "Long-horizon: fix the off-by-one bug AND add a subtract function "
            "AND add type hints. Tests the agent's ability to maintain a "
            "self-directed backlog across multiple increments, verify each, "
            "and update todo statuses as it progresses."
        ),
        fixture="py_calc_off_by_one",
        task=(
            "Run the existing tests, then drive a polish pass: (1) fix the "
            "off-by-one bug in calc.add, (2) add a subtract(a, b) function "
            "with a matching test, (3) add type hints to both functions. "
            "Use todo_write to track progress. Verify tests pass after each "
            "increment."
        ),
        max_iterations=50,
        max_seconds=360.0,
        rubric=[
            # All three sub-tasks landed
            FileContains("calc.py", "return a + b", weight=1.5),
            FileContains("calc.py", "def subtract", weight=1.5),
            FileRegex("calc.py", r"def\s+add\s*\(\s*a\s*:\s*\w+", weight=1.0),  # type hints
            CommandSucceeds("python3 -m pytest -q", weight=2.0, timeout=30),
            FileContains("test_calc.py", "subtract", weight=1.0),
            MaxToolCalls(40, weight=0.5),
        ],
        tags=["loop", "long-horizon", "multi-task"],
        pass_threshold=0.70,
    ),
]
