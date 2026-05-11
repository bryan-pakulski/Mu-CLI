"""Debug-mode benchmarks: root-cause analysis + repair + verification."""

from ..rubrics import (
    CommandSucceeds,
    FileContains,
    FileNotContains,
    MaxToolCalls,
    ResponseContains,
)
from ..spec import BenchmarkSpec


SPECS = [
    BenchmarkSpec(
        name="debug__none_dereference",
        mode="debug",
        description=(
            "A function dereferences an Optional without a None check, "
            "blowing up on a specific input. Frontier expectations: "
            "reproduce via bash, locate via search_for_string on the error "
            "message, add the guard, re-run."
        ),
        fixture="py_user_lookup_none_deref",
        task=(
            "Running `python3 -m pytest test_user.py` produces an "
            "AttributeError. Find the root cause, fix it without changing "
            "the public API, and confirm all tests pass."
        ),
        max_iterations=20,
        max_seconds=180.0,
        rubric=[
            CommandSucceeds("python3 -m pytest test_user.py -q", weight=2.0, timeout=30),
            # Defensive guard added.
            FileContains("user.py", "is None", weight=1.0),
            # Anti-pattern: did NOT just delete the failing test.
            FileContains("test_user.py", "def test_lookup_missing", weight=1.0),
            MaxToolCalls(15, weight=0.5),
            ResponseContains("None", weight=0.3),
        ],
        tags=["bug-fix", "test-driven", "guard-add"],
        pass_threshold=0.80,
    ),
]
