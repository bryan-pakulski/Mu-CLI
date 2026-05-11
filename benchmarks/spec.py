"""Benchmark specifications and result dataclasses.

A `BenchmarkSpec` is an immutable, declarative description of one
task the agent must perform. The harness reads it, copies its fixture
into a tmpdir workspace, runs the agent, and evaluates a rubric.

The rubric is a list of `Rubric` instances; each yields a
`RubricResult` (pass / partial / fail with a numeric weight). The
total `BenchmarkResult.score` is the weighted sum.

Result dataclasses are simple enough to round-trip through JSON via
`asdict` for the recorder — no custom serializer needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .rubrics import Rubric, RubricResult


@dataclass(frozen=True)
class BenchmarkSpec:
    """Declarative description of one benchmark task."""

    name: str
    mode: str  # "default" | "debug" | "feature" | "research" | "loop"
    description: str
    task: str
    # Path under `benchmarks/fixtures/` to copy as the workspace, or None
    # to run with an empty workspace.
    fixture: Optional[str] = None
    max_iterations: int = 25
    max_seconds: float = 120.0
    # Rubric must be a list of `Rubric` instances. Stored as Any to keep
    # this module import-cycle-safe; concrete type checked at runtime.
    rubric: List[Any] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    # Threshold for the boolean `passed` flag on a BenchmarkResult.
    # 1.0 = every rubric weight earned; lower allows partial credit.
    pass_threshold: float = 1.0


@dataclass
class BenchmarkRun:
    """What actually happened when the benchmark ran.

    Captured BEFORE rubric evaluation, so the rubric can inspect it.
    Tokens / costs come from the session's runtime accounting.
    """

    spec_name: str
    mode: str
    provider: str
    model: str
    started_at_unix: float
    elapsed_seconds: float
    iterations: int
    tool_call_count: int
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tokens: Dict[str, int] = field(default_factory=dict)
    cost_estimate: float = 0.0
    final_response: str = ""
    workspace_path: str = ""
    status: str = "completed"  # completed | error | max_iterations | timeout
    error: Optional[str] = None
    # Tags propagated from the spec for filtering in baseline comparisons.
    tags: List[str] = field(default_factory=list)


@dataclass
class BenchmarkResult:
    run: BenchmarkRun
    rubric_results: List["RubricResult"] = field(default_factory=list)
    score: float = 0.0  # weighted [0.0, 1.0]
    max_score: float = 0.0
    passed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe serialization for the recorder."""
        from dataclasses import asdict

        return {
            "run": asdict(self.run),
            "rubric_results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "weight": r.weight,
                    "score": r.score,
                    "message": r.message,
                }
                for r in self.rubric_results
            ],
            "score": self.score,
            "max_score": self.max_score,
            "passed": self.passed,
        }


__all__ = ["BenchmarkSpec", "BenchmarkRun", "BenchmarkResult"]
