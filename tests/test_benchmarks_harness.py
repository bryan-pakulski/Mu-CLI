"""Integration tests for `benchmarks.harness.run_benchmark`.

We drive the harness end-to-end with a *scripted* LLM provider that
returns a canned sequence of responses. No real API calls. The fixture
is a tiny synthetic workspace.
"""

import os
import shutil

import pytest

from benchmarks.harness import FIXTURES_DIR, run_benchmark
from benchmarks.rubrics import (
    CommandSucceeds,
    FileContains,
    FileNotContains,
    MaxToolCalls,
)
from benchmarks.spec import BenchmarkSpec
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _ScriptedProvider(LLMProvider):
    """Plays back a queued list of ProviderResponses, one per generate()."""

    def __init__(self, model_name, responses):
        super().__init__(model_name)
        self.name = "scripted"
        self._queue = list(responses)

    def get_available_models(self):
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        if not self._queue:
            return ProviderResponse(
                text="all done", parts=[MessagePart(type="text", text="all done")]
            )
        return self._queue.pop(0)

    def upload_file(self, *a, **kw):
        return None


# ============================================================ end-to-end


def test_harness_runs_a_no_op_benchmark_and_scores_it():
    """A benchmark with an empty fixture + a 'do nothing' agent + a single
    no-op rubric should produce a passing result with score 1.0."""
    spec = BenchmarkSpec(
        name="t_noop",
        mode="default",
        description="noop",
        fixture=None,
        task="do nothing, just say done",
        max_iterations=2,
        rubric=[MaxToolCalls(0, weight=1.0)],
        pass_threshold=1.0,
    )
    provider = _ScriptedProvider(
        "fake-model",
        [
            ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")]),
        ],
    )
    result = run_benchmark(spec, provider)
    assert result.passed is True
    assert result.score == 1.0
    assert result.run.tool_call_count == 0
    assert "done" in result.run.final_response
    assert result.run.status == "completed"
    assert result.run.provider == "scripted"
    assert result.run.model == "fake-model"


def test_harness_records_tool_call_count_from_history(tmp_path):
    spec = BenchmarkSpec(
        name="t_calls",
        mode="default",
        description="counts tool calls",
        fixture=None,
        task="list the workspace",
        max_iterations=5,
        rubric=[MaxToolCalls(5, weight=1.0)],
    )
    provider = _ScriptedProvider(
        "fake",
        [
            ProviderResponse(
                text="",
                parts=[
                    MessagePart(type="tool_call", tool_name="list_dir", tool_args={"path": "."}),
                ],
            ),
            ProviderResponse(
                text="",
                parts=[
                    MessagePart(type="tool_call", tool_name="read_file", tool_args={"filename": "x"}),
                    MessagePart(type="tool_call", tool_name="list_dir", tool_args={}),
                ],
            ),
            ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")]),
        ],
    )
    result = run_benchmark(spec, provider)
    assert result.run.tool_call_count == 3
    # All three tool names captured
    names = [tc["tool_name"] for tc in result.run.tool_calls]
    assert names == ["list_dir", "read_file", "list_dir"]


def test_harness_copies_fixture_to_isolated_workspace():
    """A fixture's files appear in the workspace; the original fixture is
    not mutated; the workspace is in a tmpdir."""
    # Use the real built-in fixture.
    spec = BenchmarkSpec(
        name="t_fixture",
        mode="default",
        description="fixture copy test",
        fixture="py_calc_off_by_one",
        task="just stop",
        max_iterations=1,
        rubric=[FileContains("calc.py", "def add", weight=1.0)],
    )
    provider = _ScriptedProvider(
        "fake",
        [ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")])],
    )
    result = run_benchmark(spec, provider, keep_workspace=True)
    workspace = result.run.workspace_path
    try:
        assert os.path.isdir(workspace)
        assert os.path.exists(os.path.join(workspace, "calc.py"))
        assert os.path.exists(os.path.join(workspace, "test_calc.py"))
        assert workspace != os.path.join(FIXTURES_DIR, "py_calc_off_by_one"), (
            "harness must not point at the original fixture dir"
        )
        # Original fixture untouched
        original_calc = os.path.join(FIXTURES_DIR, "py_calc_off_by_one", "calc.py")
        with open(original_calc) as fh:
            assert "a + b + 1" in fh.read()  # bug still planted
        assert result.passed is True
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_harness_workspace_is_cleaned_up_by_default():
    spec = BenchmarkSpec(
        name="t_cleanup",
        mode="default",
        description="cleanup test",
        fixture="py_calc_off_by_one",
        task="stop",
        max_iterations=1,
        rubric=[],
    )
    provider = _ScriptedProvider(
        "fake",
        [ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")])],
    )
    result = run_benchmark(spec, provider)
    assert not os.path.exists(result.run.workspace_path), (
        "tmpdir should be removed when keep_workspace=False"
    )


def test_harness_handles_provider_exception_gracefully():
    class _Boom(LLMProvider):
        name = "boom"
        def get_available_models(self): return ["x"]
        def generate(self, *a, **kw): raise RuntimeError("kaboom")
        def upload_file(self, *a, **kw): return None

    spec = BenchmarkSpec(
        name="t_boom",
        mode="default",
        description="provider crash",
        fixture=None,
        task="x",
        rubric=[FileContains("x", "y", weight=1.0)],
    )
    result = run_benchmark(spec, _Boom("x"))
    assert result.passed is False
    # Either the provider raised and got caught, or send_message returned
    # status="error". Both produce a graceful failure surface.
    assert result.run.status in ("error", "completed")


def test_harness_evaluates_rubric_against_real_workspace_state(tmp_path):
    """If the agent actually edits files (here, simulated via a write_file
    tool call), the rubric sees the edited state."""
    spec = BenchmarkSpec(
        name="t_edit",
        mode="default",
        description="agent edits a file",
        fixture="py_calc_off_by_one",
        task="rewrite calc.py to return a + b",
        max_iterations=3,
        rubric=[
            FileContains("calc.py", "return a + b", weight=1.0),
            FileNotContains("calc.py", "+ 1", weight=1.0),
        ],
    )
    # The scripted child issues a write_file then a final text response.
    provider = _ScriptedProvider(
        "fake",
        [
            ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="write_file",
                        tool_args={
                            "filename": "calc.py",
                            "content": "def add(a, b):\n    return a + b\n",
                        },
                    )
                ],
            ),
            ProviderResponse(text="fixed", parts=[MessagePart(type="text", text="fixed")]),
        ],
    )
    result = run_benchmark(spec, provider, keep_workspace=False)
    assert result.passed is True
    assert result.score == 1.0


def test_harness_missing_fixture_raises():
    spec = BenchmarkSpec(
        name="t_bad_fixture",
        mode="default",
        description="missing fixture",
        fixture="this_fixture_does_not_exist",
        task="x",
        rubric=[],
    )
    provider = _ScriptedProvider(
        "fake",
        [ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")])],
    )
    with pytest.raises(FileNotFoundError):
        run_benchmark(spec, provider)


# ============================================================ scoring math


def test_partial_credit_yields_fractional_score():
    spec = BenchmarkSpec(
        name="t_partial",
        mode="default",
        description="2 rubrics, one passes one fails → 0.5",
        fixture=None,
        task="stop",
        max_iterations=1,
        rubric=[
            MaxToolCalls(0, weight=1.0),
            FileContains("never_exists.txt", "x", weight=1.0),
        ],
        pass_threshold=1.0,
    )
    provider = _ScriptedProvider(
        "fake",
        [ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")])],
    )
    result = run_benchmark(spec, provider)
    assert result.score == pytest.approx(0.5)
    assert result.passed is False  # below 1.0 threshold


def test_pass_threshold_below_one_allows_partial_credit():
    spec = BenchmarkSpec(
        name="t_threshold",
        mode="default",
        description="0.5 score with 0.5 threshold = pass",
        fixture=None,
        task="stop",
        max_iterations=1,
        rubric=[
            MaxToolCalls(0, weight=1.0),
            FileContains("never_exists.txt", "x", weight=1.0),
        ],
        pass_threshold=0.5,
    )
    provider = _ScriptedProvider(
        "fake",
        [ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")])],
    )
    result = run_benchmark(spec, provider)
    assert result.score == pytest.approx(0.5)
    assert result.passed is True


# ============================================================ spec catalogue


def test_built_in_specs_loadable_and_unique_names():
    from benchmarks.specs import ALL_SPECS, by_mode

    assert len(ALL_SPECS) >= 5, f"expected ≥5 built-in specs, found {len(ALL_SPECS)}"
    names = [s.name for s in ALL_SPECS]
    assert len(names) == len(set(names)), "spec names must be unique"
    # One spec per mode minimum
    for mode in ("default", "debug", "feature", "research", "loop"):
        assert by_mode(mode), f"no built-in spec for mode {mode!r}"


def test_built_in_specs_reference_valid_fixtures():
    """Every fixture mentioned by a built-in spec must exist on disk."""
    from benchmarks.specs import ALL_SPECS

    for spec in ALL_SPECS:
        if spec.fixture is None:
            continue
        path = os.path.join(FIXTURES_DIR, spec.fixture)
        assert os.path.isdir(path), (
            f"spec {spec.name!r} references missing fixture: {spec.fixture}"
        )
