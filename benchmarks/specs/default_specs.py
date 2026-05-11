"""Default-mode benchmarks: general coding assistance."""

from ..rubrics import (
    CommandSucceeds,
    FileContains,
    MaxSeconds,
    MaxToolCalls,
    ResponseContains,
)
from ..spec import BenchmarkSpec


SPECS = [
    BenchmarkSpec(
        name="default__fix_off_by_one",
        mode="default",
        description=(
            "Fix an off-by-one bug in calc.add (returns a + b + 1) so the "
            "existing test suite passes. Frontier expectations: locate via "
            "search rather than reading every file, apply a surgical diff, "
            "verify with bash test run."
        ),
        fixture="py_calc_off_by_one",
        task=(
            "There's a failing test in test_calc.py. Find the bug in calc.py, "
            "fix it surgically, and confirm the tests pass by running pytest."
        ),
        max_iterations=20,
        max_seconds=180.0,
        rubric=[
            CommandSucceeds("python3 -m pytest test_calc.py -q", weight=2.0, timeout=30),
            FileContains("calc.py", "return a + b", weight=1.0),
            MaxToolCalls(12, weight=0.5),
            MaxSeconds(120.0, weight=0.5),
            ResponseContains("pass", weight=0.5),
        ],
        tags=["fs-edit", "test-verification", "small-bug-fix"],
        pass_threshold=0.85,
    ),
]
