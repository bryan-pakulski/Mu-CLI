"""Tool-output efficiency hook-up (spec #1/#11/#12).

Regression coverage for the two gaps that made "tokens saved" read ~0 on
real traces:

  * tools outside the ToolResultCache read allowlist (bash & co.) never got
    a store key, so the observation transform kept their full raw inline;
  * the provider projection stripped ``stored_ref`` / ``omission_note`` from
    observed results, so the model could not recall what was elided.
"""

import json

from mu.session.messages import _lean_tool_result
from mu.session.tool_cache import ToolResultCache
from mu.session.tools_glue import (
    build_structured_tool_result,
    result_exceeds_inline_budget,
)
from mu.tools._observe import NEVER_OBSERVE_TOOLS, observation_eligible


class _FakeSession:
    def __init__(self, variables):
        self.variables = variables
        self.tool_result_cache = ToolResultCache()

    def _unwrap_tool_envelope(self, raw):
        if isinstance(raw, str) and raw.startswith("{"):
            try:
                env = json.loads(raw)
                if isinstance(env, dict) and "ok" in env:
                    return env, env.get("message", "")
            except ValueError:
                pass
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
    "structured_tool_results": True,
}

BIG_LOG = "".join(f"[{i:04d}] compiling module_{i}.c ... ok\n" for i in range(300))


def test_bash_over_budget_exceeds_inline_budget():
    sess = _FakeSession(VARS)
    assert result_exceeds_inline_budget(sess, "bash", BIG_LOG) is True


def test_small_bash_result_stays_below_budget():
    sess = _FakeSession(VARS)
    assert result_exceeds_inline_budget(sess, "bash", "exit=0\nok\n") is False


def test_error_results_use_failure_budget():
    """A failing bash result gets the larger failure budget before observation."""
    sess = _FakeSession(VARS)
    body = "Traceback (most recent call last):\n" + "  File x, line 1\n" * 60
    env = json.dumps({"ok": False, "error_code": "tool_error", "message": body})
    # ~300 tokens: over the 256 success budget, under the 1024 failure budget.
    assert result_exceeds_inline_budget(sess, "bash", env) is False


def test_retrieval_tools_are_never_observed():
    sess = _FakeSession(VARS)
    for name in ("recall", "result_range", "result_tail", "flush"):
        assert name in NEVER_OBSERVE_TOOLS
        assert observation_eligible(name) is False
        assert result_exceeds_inline_budget(sess, name, BIG_LOG) is False
    assert observation_eligible("bash") is True
    assert observation_eligible("await_subagent") is True


def test_force_store_gives_bash_a_key_and_observation_fires():
    """End-to-end shape of the loop_body decision: the allowlist store returns
    None for bash, the budget check says 'over', the forced store yields a key,
    and the structured envelope is then delivered as an observation."""
    sess = _FakeSession(VARS)
    cache = sess.tool_result_cache
    assert cache.store_with_locator(
        call_id="c1", tool_name="bash", tool_args={"command": "make"}, result=BIG_LOG
    ) is None
    assert result_exceeds_inline_budget(sess, "bash", BIG_LOG)
    key = cache.store(call_id="c1", tool_name="bash", result=BIG_LOG, force=True)
    assert key
    assert cache.recall(key)["result"] == BIG_LOG

    res = build_structured_tool_result(
        sess, "bash", {"command": "make"}, BIG_LOG, cache_key=key
    )
    assert res["raw"] is None
    assert res["telemetry"]["delivery_mode"] == "observed"
    assert res["data"]["stored_ref"] == key
    assert res["data"]["omitted"] is True
    tele = res["telemetry"]
    assert tele["injected_token_count"] < tele["raw_token_count"]
    assert tele["compression_ratio"] > 0.5


def test_plain_store_never_makes_bash_auto_recallable():
    """Force-storing must not turn bash into a locator/auto-recall candidate."""
    cache = ToolResultCache()
    key = cache.store(
        call_id="c1", tool_name="bash", result=BIG_LOG, force=True
    )
    assert key
    assert cache.lookup_by_locator("bash", {"command": "make"}) is None
    assert cache._range_key("bash", {"path": "/tmp/x"}) is None


def test_lean_projection_keeps_stored_ref_for_observed_results():
    sess = _FakeSession(VARS)
    res = build_structured_tool_result(
        sess, "bash", {"command": "make"}, BIG_LOG, cache_key="KEY42"
    )
    lean = _lean_tool_result(res)
    assert "raw" not in lean
    assert lean["data"]["stored_ref"] == "KEY42"
    assert "omission_note" in lean["data"]
    assert "content omitted" in lean["data"]["omission_note"]
    assert "excerpt" in lean["data"]
    # Boilerplate the model doesn't need still goes.
    assert "retrievable_via" not in lean["data"]
    assert "preview" not in lean["data"]


def test_lean_projection_still_strips_ref_for_verbatim_results():
    lean = _lean_tool_result(
        {
            "ok": True,
            "raw": "short",
            "data": {"omitted": False, "stored_ref": "k", "omission_note": "n",
                     "line_count": 1},
        }
    )
    assert lean["raw"] == "short"
    assert lean["data"] == {"line_count": 1}


def test_injected_count_measures_provider_projection():
    """injected_token_count must be what the provider receives (the lean
    projection), so savings are never reported as negative."""
    sess = _FakeSession(VARS)
    res = build_structured_tool_result(
        sess, "search_for_string", {"string": "x"}, BIG_LOG, cache_key="K"
    )
    from utils.token_estimator import estimate_tokens

    projected = json.dumps(_lean_tool_result(res), indent=2, sort_keys=True, default=str)
    assert res["telemetry"]["injected_token_count"] == int(estimate_tokens(projected))
    assert res["telemetry"]["injected_token_count"] < res["telemetry"]["raw_token_count"]


def test_default_budgets_for_model_facing_prose():
    from utils.config import DEFAULT_VARIABLES
    from mu.tools._observe import resolve_inline_budget

    budgets = DEFAULT_VARIABLES["tool_inline_budgets"]
    assert budgets["await_subagent"] >= 2048
    assert budgets["search_memory"] >= 1024
    assert resolve_inline_budget("await_subagent", False, DEFAULT_VARIABLES) >= 2048
    # Raw logs keep the global default.
    assert resolve_inline_budget("bash", False, DEFAULT_VARIABLES) == DEFAULT_VARIABLES[
        "tool_result_inline_budget"
    ]
