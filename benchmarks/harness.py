"""Benchmark harness.

`run_benchmark(spec, provider)` drives one benchmark end-to-end:

  1. Copies `benchmarks/fixtures/<spec.fixture>/` to a fresh tmpdir.
  2. Builds a `Session` with the supplied provider, sets `agent_mode`
     to `spec.mode`, `yolo=True`, and `max_iterations=spec.max_iterations`.
  3. Calls `session.send_message(spec.task)` and measures wall time,
     iteration count, tool call count, and token usage.
  4. Constructs a `BenchmarkRun` capturing every observable signal.
  5. Evaluates each rubric against the run; computes weighted score
     and `passed` boolean using `spec.pass_threshold`.

The harness deliberately does not interpret the provider's output
beyond capturing it — semantics live entirely in the rubrics, so a
benchmark author can score on any combination of file state, test
results, response content, and resource usage.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from typing import Any, Optional

from .rubrics import RubricResult
from .spec import BenchmarkResult, BenchmarkRun, BenchmarkSpec


logger = logging.getLogger("mucli.benchmarks")


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _copy_fixture(fixture: Optional[str]) -> str:
    """Copy a fixture template to a tmpdir and return its path.

    If `fixture` is None, returns a fresh empty tmpdir.
    """
    tmpdir = tempfile.mkdtemp(prefix="mucli_bench_")
    if not fixture:
        return tmpdir
    src = os.path.join(FIXTURES_DIR, fixture)
    if not os.path.isdir(src):
        raise FileNotFoundError(
            f"benchmark fixture not found: {src} "
            f"(expected directory under {FIXTURES_DIR})"
        )
    # Copy contents of `src` into `tmpdir` (not nested inside).
    for entry in os.listdir(src):
        s = os.path.join(src, entry)
        d = os.path.join(tmpdir, entry)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    return tmpdir


def _build_session(spec: BenchmarkSpec, provider: Any, workspace: str):
    """Construct a benchmark Session. Inline import to keep the
    `benchmarks` package importable even when the legacy core isn't
    initialised (e.g. in CI lint-only runs).

    Important: the workspace must be wired up on the *SessionManager*
    before constructing the Session, because `Session.__init__` calls
    `sync_runtime_state()` which reads `self.session_manager.folder_context`
    (and then does `os.chdir` to the first folder). Setting it on the
    Session post-construction is wiped on the next sync.
    """
    from core.session import Session, SessionManager

    sm = SessionManager(session_name=f"__bench_{spec.name}__")
    # Don't persist benchmark sessions to disk.
    sm.save_history = lambda *a, **kw: None
    # Attach the workspace BEFORE Session() so sync_runtime_state picks it up.
    sm.folder_context.add_folder(workspace)

    sess = Session(
        provider=provider,
        thinking=False,
        system_instruction="You are an automated benchmark subject. Be concise.",
        session_manager=sm,
    )
    # Defensive: chdir if the Session's __init__ didn't (e.g. ui=None branch
    # silenced the announcement but should still have chdir'd).
    if os.getcwd() != workspace:
        try:
            os.chdir(workspace)
        except OSError:
            pass

    sess.variables["yolo"] = True
    sess.variables["agent_mode"] = spec.mode
    sess.variables["max_iterations"] = int(spec.max_iterations)
    # Disable history compaction — short runs, no need to roll mid-bench.
    sess.variables["compact_history"] = False
    return sess


def _count_tool_calls(history: list) -> tuple:
    """Walk session history, return (count, list of {tool_name, tool_args})."""
    count = 0
    calls = []
    for entry in history:
        if entry.get("role") != "assistant":
            continue
        for part in entry.get("parts", []) or []:
            if part.get("type") == "tool_call":
                count += 1
                calls.append(
                    {
                        "tool_name": part.get("tool_name", ""),
                        "tool_args": part.get("tool_args", {}),
                    }
                )
    return count, calls


def _evaluate_rubric(spec: BenchmarkSpec, run: BenchmarkRun) -> tuple:
    """Apply each rubric. Returns (results, score, max_score)."""
    results = []
    earned = 0.0
    total = 0.0
    for rubric in spec.rubric:
        try:
            res = rubric.evaluate(run)
        except Exception as exc:  # pragma: no cover — defensive
            res = RubricResult(
                name=getattr(rubric, "name", type(rubric).__name__),
                passed=False,
                weight=getattr(rubric, "weight", 1.0),
                score=0.0,
                message=f"rubric raised: {exc}",
            )
        results.append(res)
        earned += res.score
        total += res.weight
    return results, earned, total


def run_benchmark(
    spec: BenchmarkSpec,
    provider: Any,
    *,
    keep_workspace: bool = False,
) -> BenchmarkResult:
    """Run a single benchmark end-to-end and score it.

    Args:
      spec:           the BenchmarkSpec to run.
      provider:       an LLMProvider instance (already configured with
                      model + credentials).
      keep_workspace: if True, the tmpdir is NOT cleaned up (useful when
                      debugging a failing benchmark).

    Returns:
      BenchmarkResult with run metrics + rubric results + score.
    """
    workspace = _copy_fixture(spec.fixture)
    # Save cwd so we can restore it in `finally` — Session.__init__ chdirs into
    # the workspace, and if we don't restore, the harness leaves cwd dangling
    # in a (possibly soon-deleted) tmpdir.
    original_cwd = None
    try:
        original_cwd = os.getcwd()
    except (OSError, FileNotFoundError):
        original_cwd = os.path.dirname(os.path.abspath(__file__))

    started_at_unix = time.time()
    started = time.monotonic()
    error: Optional[str] = None
    final_response = ""
    iterations = 0
    tokens = {}
    cost_estimate = 0.0
    status = "completed"
    history: list = []

    try:
        session = _build_session(spec, provider, workspace)

        # Enforce wall-clock budget via a simple watchdog: if the LLM hangs
        # we'd block here, so this is best-effort. Real timeouts require
        # threading; for v1 we rely on the underlying provider timeout.
        result = session.send_message(spec.task)

        final_response = str(result.get("assistant_text") or "").strip()
        iterations = int(
            result.get("iterations")
            or len(
                [
                    m
                    for m in session.session_manager.history
                    if m.get("role") == "assistant"
                ]
            )
        )
        tokens = dict(result.get("tokens") or {})
        cost_estimate = float((tokens.get("estimated_cost") or 0.0))
        status = str(result.get("status") or "completed")
        if status != "completed":
            error = str(result.get("error") or "")
        history = list(session.session_manager.history)
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("benchmark %s raised: %s", spec.name, exc)
        error = str(exc)
        status = "error"

    elapsed = time.monotonic() - started
    tool_call_count, tool_calls = _count_tool_calls(history)

    run = BenchmarkRun(
        spec_name=spec.name,
        mode=spec.mode,
        provider=getattr(provider, "name", "unknown"),
        model=getattr(provider, "model_name", "unknown"),
        started_at_unix=started_at_unix,
        elapsed_seconds=elapsed,
        iterations=iterations,
        tool_call_count=tool_call_count,
        tool_calls=tool_calls,
        tokens=tokens,
        cost_estimate=cost_estimate,
        final_response=final_response,
        workspace_path=workspace,
        status=status,
        error=error,
        tags=list(spec.tags),
    )

    rubric_results, earned, total = _evaluate_rubric(spec, run)
    # Normalize score to [0, 1] regardless of weight scaling.
    norm_score = earned / total if total > 0 else 0.0
    passed = norm_score >= spec.pass_threshold and run.status == "completed"

    if not keep_workspace:
        try:
            shutil.rmtree(workspace, ignore_errors=True)
        except Exception:  # pragma: no cover — defensive
            pass

    # Restore the caller's cwd. Session.__init__ chdir'd into the workspace,
    # which we may have just deleted — leaving cwd dangling would break any
    # subsequent code that does os.getcwd().
    if original_cwd is not None:
        try:
            os.chdir(original_cwd)
        except OSError:
            # Caller's cwd is gone too — fall back to the benchmarks pkg dir.
            os.chdir(os.path.dirname(os.path.abspath(__file__)))

    return BenchmarkResult(
        run=run,
        rubric_results=rubric_results,
        score=norm_score,
        max_score=total,
        passed=passed,
    )


__all__ = ["FIXTURES_DIR", "run_benchmark"]
