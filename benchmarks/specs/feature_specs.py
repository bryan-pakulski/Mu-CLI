"""Feature-mode benchmarks: phased plan engine + bounded implementation."""

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
        name="feature__add_calc_subtract",
        mode="feature",
        description=(
            "Use the Feature Plan Engine to add a `subtract` function to "
            "calc.py with a matching test. Frontier expectations: create a "
            "feature plan with Exit Criteria, work one task at a time, "
            "verify tests pass before marking complete."
        ),
        fixture="py_calc_off_by_one",
        task=(
            "Add a `subtract(a, b)` function to calc.py that returns a - b, "
            "plus a test for it in test_calc.py. Use the feature engine; "
            "every task should have Exit Criteria; verify with pytest."
        ),
        max_iterations=40,
        max_seconds=300.0,
        rubric=[
            # The feature itself
            FileRegex("calc.py", r"def\s+subtract\s*\(\s*a\s*,\s*b\s*\)\s*:", weight=1.5),
            FileContains("calc.py", "return a - b", weight=1.0),
            # The test
            FileContains("test_calc.py", "subtract", weight=1.0),
            CommandSucceeds("python3 -m pytest test_calc.py -q", weight=2.0, timeout=30),
            # Feature-mode produced canonical metadata
            FileContains("calc.py", "def subtract", weight=0.5),
            MaxToolCalls(25, weight=0.5),
        ],
        tags=["feature-engine", "small-feature", "test-creation"],
        pass_threshold=0.75,
    ),
]
