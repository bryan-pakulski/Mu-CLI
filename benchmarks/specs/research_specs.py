"""Research-mode benchmarks: code exploration + citation discipline."""

from ..rubrics import (
    FileContains,
    MaxSeconds,
    MaxToolCalls,
    ResponseContains,
    ResponseMatches,
)
from ..spec import BenchmarkSpec


SPECS = [
    BenchmarkSpec(
        name="research__explain_calc_module",
        mode="research",
        description=(
            "Explain what calc.py does without changing anything. Frontier "
            "expectations: read the file(s), produce a concise structured "
            "summary covering the public API, current state of any tests, "
            "and notable issues if present."
        ),
        fixture="py_calc_off_by_one",
        task=(
            "Read calc.py and test_calc.py and produce a short technical "
            "summary: what does the module do, what is its public API, are "
            "there any bugs visible in the current code? Do NOT modify any "
            "files."
        ),
        max_iterations=15,
        max_seconds=120.0,
        rubric=[
            # The response must reference the function names by exact name.
            ResponseContains("add", weight=1.0),
            # Hint that the agent noticed the bug.
            ResponseMatches(r"(off[- ]by[- ]one|bug|incorrect|wrong)", weight=1.0),
            # Did NOT modify the workspace.
            FileContains("calc.py", "return a + b + 1", weight=1.0),  # original bug intact
            MaxToolCalls(10, weight=0.5),
            MaxSeconds(60.0, weight=0.5),
        ],
        tags=["research", "readonly", "explanation"],
        pass_threshold=0.75,
    ),
]
