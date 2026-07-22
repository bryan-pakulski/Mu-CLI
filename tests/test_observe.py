"""Budget-thresholded observation at delivery (spec #1/#2/#10)."""

from types import SimpleNamespace

from mu.session.tools_glue import build_structured_tool_result
from mu.tools._observe import build_observation, resolve_inline_budget, _excerpt


class _FakeSession:
    """Minimal session stub with the helpers build_structured_tool_result uses."""

    def __init__(self, variables):
        self.variables = variables

    def _unwrap_tool_envelope(self, raw):
        return ({}, raw)

    def _parse_search_results(self, raw):
        return {"match_count": 0, "matches": []}

    def _parse_list_dir(self, raw, path):
        return {"entries": 0}

    def _parse_workspace_details(self, raw):
        return {}

    def _parse_json_result(self, raw):
        return {}


VARS = {
    "tool_result_inline_budget": 256,
    "tool_result_failure_budget": 1024,
    "tool_inline_budgets": {},
}


def test_small_result_stays_verbatim():
    sess = _FakeSession(VARS)
    res = build_structured_tool_result(
        sess, "read_file", {"filename": "a.py"}, "short content",
        cache_key="k1",
    )
    assert res["raw"] == "short content"
    assert res["data"]["omitted"] is False
    assert res["telemetry"]["injected_token_count"] > 0
    assert res["telemetry"].get("compression_ratio") is None


def test_large_result_is_observed_with_stored_ref():
    sess = _FakeSession(VARS)
    big = "line of file content\n" * 400  # well over 256 tokens
    res = build_structured_tool_result(
        sess, "read_file", {"filename": "a.py"}, big, cache_key="KEYABC",
    )
    assert res["raw"] is None  # full raw NOT in context
    assert res["data"]["omitted"] is True
    assert res["data"]["stored_ref"] == "KEYABC"
    assert "recall" in res["data"]["retrievable_via"]
    assert "omission_note" in res["data"]
    assert res["telemetry"]["delivery_mode"] == "observed"
    assert res["telemetry"]["compression_ratio"] >= 0.0
    # Injected should be far smaller than the raw token count.
    assert (
        res["telemetry"]["injected_token_count"]
        < res["telemetry"]["raw_token_count"]
    )


def test_large_result_without_cache_key_keeps_raw():
    """No store backing → can't offer a stored_ref, keep raw inline (safe)."""
    sess = _FakeSession(VARS)
    big = "line of file content\n" * 400
    res = build_structured_tool_result(
        sess, "read_file", {"filename": "a.py"}, big, cache_key=None,
    )
    assert res["raw"] == big
    assert res["data"]["omitted"] is False


def test_failure_budget_larger_than_routine():
    assert resolve_inline_budget("bash", True, VARS) == 1024
    assert resolve_inline_budget("bash", False, VARS) == 256


def test_per_tool_override():
    vars_ov = dict(VARS)
    vars_ov["tool_inline_budgets"] = {"read_file": 64}
    assert resolve_inline_budget("read_file", False, vars_ov) == 64


def test_bash_observation_carries_diagnostics_and_exit():
    raw = "exit=1\nError: build failed\nError: build failed\nwarning: unused\nok line\n" * 50
    obs, note = build_observation(
        "bash", None, raw, {}, budget_tokens=256, is_error=True,
    )
    assert obs["omitted"] is True
    assert "exit_code" in obs and obs["exit_code"] == 1
    # Deduped: "Error: build failed" appears once.
    diags = obs["diagnostics"]
    assert diags.count("Error: build failed") == 1
    assert "omitted" in note


def test_excerpt_head_tail_with_marker():
    text = "HEAD" + ("x" * 2000) + "TAIL"
    ex = _excerpt(text, 50)
    assert "HEAD" in ex
    assert "TAIL" in ex
    assert "omitted" in ex