"""Phase 2 of the harness latency plan: batched reads come back in the same turn, tool results
stay inline up to a model-scaled budget, and an "observation" never inflates a result."""
import json

from mu.session.session import Session, SessionManager
from mu.workspace.folder_context import FolderContext
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _Provider(LLMProvider):
    """Turn 1: the scripted tool calls. Turn 2: records what it was sent, then answers."""

    def __init__(self, calls, window=None):
        super().__init__("scripted")
        self.name = "scripted"
        self.calls, self.window, self.seen, self.n = list(calls), window, [], 0

    def get_available_models(self):
        return ["scripted"]

    def effective_context_window(self, model_name=None):
        return self.window

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.n += 1
        self.seen.append(messages)
        if self.n == 1:
            return ProviderResponse(text="", parts=[MessagePart(type="tool_call", tool_name=name, tool_args=args)
                                                    for name, args in self.calls], input_tokens=1, output_tokens=1, total_tokens=2)
        return ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")])

    def upload_file(self, *a, **kw):
        return None


def _session(tmp_path, provider, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    sess = Session(provider, False, "system", SessionManager())
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    sess.folder_context = fc
    sess.sync_runtime_state()
    sess.variables["yolo"] = True
    return sess


def _second_request_text(provider):
    return json.dumps([[p.__dict__ for p in m.parts] for m in provider.seen[1]], default=str)


def test_batched_reads_are_delivered_inline_on_large_context_models(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("ALPHA = 1\n")
    (tmp_path / "b.py").write_text("BETA = 2\n")
    provider = _Provider([("read_file", {"filename": str(tmp_path / "a.py")}), ("read_file", {"filename": str(tmp_path / "b.py")})],
                         window=1_000_000)
    _session(tmp_path, provider, monkeypatch).send_message("read both")
    sent = _second_request_text(provider)
    assert "ALPHA = 1" in sent and "BETA = 2" in sent
    assert "collation buffer" not in sent
    assert provider.n == 2  # no extra `flush` turn


def test_small_context_models_still_collate_large_batches(tmp_path, monkeypatch):
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("\n".join(f"VALUE_{i} = {i}" for i in range(400)) + "\n")
    provider = _Provider([("read_file", {"filename": str(tmp_path / "a.py")}), ("read_file", {"filename": str(tmp_path / "b.py")})],
                         window=8_000)
    _session(tmp_path, provider, monkeypatch).send_message("read both")
    assert "collation buffer" in _second_request_text(provider)


def test_inline_budget_scales_with_the_context_window():
    from mu.tools._observe import resolve_inline_budget
    variables = {"tool_result_inline_budget": 4000, "tool_inline_budgets": {"read_file": 8000}, "tool_result_inline_share": 0.05}
    assert resolve_inline_budget("bash", False, variables, 1_000_000) == 4000
    assert resolve_inline_budget("read_file", False, variables, 1_000_000) == 8000
    assert resolve_inline_budget("read_file", False, variables, 16_000) == 800
    assert resolve_inline_budget("bash", False, variables, 2_000) == 256  # floor
    assert resolve_inline_budget("bash", False, variables) == 4000  # window unknown


def test_collation_threshold_follows_the_model_window():
    from types import SimpleNamespace
    from mu.agent.loop_body import collation_threshold_tokens
    session = lambda window: SimpleNamespace(variables={}, provider=SimpleNamespace(
        model_name="m", effective_context_window=lambda name: window))
    assert collation_threshold_tokens(session(1_000_000)) == 24000
    assert collation_threshold_tokens(session(32_000)) == 3200


def test_observation_never_inflates_a_result(tmp_path, monkeypatch):
    from mu.session import tools_glue
    import mu.tools._observe as observe
    structured = {"tool_name": "bash_status", "data": {"text": "x" * 2000}, "raw": "x" * 2000,
                  "telemetry": {}}
    monkeypatch.setattr(observe, "build_observation",
                        lambda *a, **k: ({"summary": "y" * 20000}, "note"))
    session = type("S", (), {"variables": {"tool_result_inline_budget": 64}, "provider": None})()
    tools_glue._apply_observation_transform(session, structured, "x" * 2000, "key-1", False)
    assert structured["raw"] == "x" * 2000 and structured["data"]["text"] == "x" * 2000
    assert structured["data"]["omitted"] is False and "delivery_mode" not in structured["telemetry"]


# ---------------------------------------------------------------- Phase 4: parallel read-only bash


def test_read_only_shell_commands_are_recognised():
    from mu.agent.parallel import is_read_only_command
    for command in ("ls -la src", "rg -n 'def x' mu | head -20", "git diff HEAD~1 -- mu", "git log --oneline -5",
                    "find . -name '*.py'", "wc -l a.py b.py", "git status --short"):
        assert is_read_only_command(command), command
    for command in ("cat a > b", "rm -rf build", "find . -delete", "find . -exec rm {} ;", "npm test",
                    "git commit -m x", "ls && rm x", "echo $(id)", "tree -o out.txt", "rg --pre ./x foo",
                    "git branch -D main", "sort -o x y", "tee out"):
        assert not is_read_only_command(command), command


def test_read_only_bash_is_parallel_only_without_approval_prompts():
    from mu.agent.parallel import is_parallel_safe_call
    args = {"command": "rg foo"}
    assert is_parallel_safe_call("bash", args, approvals_needed=False)
    assert not is_parallel_safe_call("bash", args, approvals_needed=True)
    assert not is_parallel_safe_call("bash", {"command": "make"}, approvals_needed=False)
    assert is_parallel_safe_call("read_file", {}, approvals_needed=True)


def test_segments_keep_order_and_use_barriers():
    from mu.agent.parallel import execution_segments
    calls = [(0, ("read_file", {})), (1, ("bash", {})), (2, ("write_file", {})), (3, ("read_file", {})),
             (4, ("list_dir", {})), (5, ("flush", {})), (6, ("read_file", {}))]
    safe = lambda name, args: name in ("read_file", "list_dir", "bash")
    assert execution_segments(calls, safe) == [([0, 1], []), ([], [2]), ([3, 4], []), ([], [5]), ([], [6])]


def test_read_only_bash_calls_run_concurrently_in_the_loop(tmp_path, monkeypatch):
    import threading
    import time
    provider = _Provider([("bash", {"command": "ls"}), ("bash", {"command": "git status --short"}),
                          ("bash", {"command": "wc -l *.py"})], window=1_000_000)
    session = _session(tmp_path, provider, monkeypatch)
    threads, orig = set(), session._execute_tool_with_memory

    def slow(name, args, **kw):
        threads.add(threading.current_thread().name)
        time.sleep(0.2)
        return orig(name, args, **kw)
    session._execute_tool_with_memory = slow
    started = time.monotonic()
    session.send_message("look around")
    assert len(threads) == 3  # three worker threads, not the agent thread three times
    assert time.monotonic() - started < 0.55


def test_effort_setting_reaches_the_provider_request():
    from types import SimpleNamespace
    from mu.agent.retry import _effort_kwargs

    class Capable:
        def stream(self, messages, system_prompt=None, thinking=False, tools=None, reasoning_effort=None):
            pass

    class Legacy:
        def stream(self, messages, system_prompt=None, thinking=False, tools=None):
            pass
    session = lambda effort, provider: SimpleNamespace(variables={"reasoning_effort": effort}, provider=provider)
    assert _effort_kwargs(session("low", Capable())) == {"reasoning_effort": "low"}
    assert _effort_kwargs(session("", Capable())) == {}
    assert _effort_kwargs(session("extreme", Capable())) == {}
    assert _effort_kwargs(session("high", Legacy())) == {}
    from test_anthropic_provider import _provider
    from providers.base import Message, MessagePart
    request = _provider("claude-opus-4-8")._build_request([Message("user", [MessagePart("text", text="hi")])],
                                                          None, False, None, None, "low")
    assert request["output_config"] == {"effort": "low"}


def test_effort_command(tmp_path, monkeypatch):
    from mu.commands.variables import effort_cmd
    session = _session(tmp_path, _Provider([]), monkeypatch)
    assert effort_cmd(session, "medium", allow_prompt=False).ok and session.variables["reasoning_effort"] == "medium"
    assert not effort_cmd(session, "turbo", allow_prompt=False).ok
    assert effort_cmd(session, "default", allow_prompt=False).ok and session.variables["reasoning_effort"] == ""


def test_job_stage_effort_is_a_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    from mu.jobs.job_settings import FIELD_BY_PATH, save_settings
    from mu.jobs.sandbox.runner import _stage_effort
    assert FIELD_BY_PATH["jobs.stage_effort.review"]["default"] == ""
    save_settings({"jobs": {"stage_effort": {"review": "medium"}}})
    assert _stage_effort("review") == "medium" and _stage_effort("plan") == ""


# ---------------------------------------------------------------- Phase 5.4: per-iteration overhead


def test_memory_ranking_stays_fast_and_equivalent_for_huge_queries():
    import time
    from mu.memory.stores import TaskMemoryStore
    store = TaskMemoryStore()
    for i in range(30):
        store.save(f"finding {i}: the compactor drift applies to provider {i % 3}", tags=["note"])
    huge = "review this patch " + " ".join(f"token{i}" for i in range(30000)) + " compactor drift"
    started = time.monotonic()
    ranked = store._rank_by_relevance(huge, status_set=None, kind_set=None, exclude_tags=set())
    assert time.monotonic() - started < 0.5
    assert ranked[0][0] > 0  # "compactor"/"drift" still score
    small = store._rank_by_relevance("compactor drift", status_set=None, kind_set=None, exclude_tags=set())
    assert all(score == 6 for score, _t, _e in small)  # 2 terms x (haystack 2 + content 1)


def test_token_estimates_are_memoized_without_changing_counts():
    from utils.token_estimator import _memo_count, estimate_tokens
    text = "def handler(x):\n    return x * 2\n" * 200
    first = estimate_tokens(text)
    hits = _memo_count.cache_info().hits
    assert estimate_tokens(text) == first and _memo_count.cache_info().hits == hits + 1
    assert estimate_tokens("short text") == 2


def test_skill_trigger_matching_is_memoized():
    import re
    from types import SimpleNamespace
    from mu.skills import _trigger_search, match_trigger
    skill = SimpleNamespace(trigger_regex=re.compile(r"\breview\b", re.I))
    text = "please review " + "x " * 50000
    assert match_trigger(skill, text)
    hits = _trigger_search.cache_info().hits
    assert match_trigger(skill, text) and _trigger_search.cache_info().hits == hits + 1


def test_history_window_is_trimmed_in_steps_not_every_turn(tmp_path, monkeypatch):
    """Phase 1 (secondary): a window that slides by one message per turn would shift the whole
    prompt prefix; trimming to a low watermark keeps the start stable for several turns."""
    from mu.session.messages import prepare_runtime_history
    session = _session(tmp_path, _Provider([]), monkeypatch)
    sm = session.session_manager
    session._compaction_token_budget = lambda: 2000
    starts = []
    for i in range(60):
        sm.history.append({"role": "user" if i % 2 == 0 else "assistant",
                           "parts": [{"type": "text", "text": f"message {i} " + "word " * 40}]})
        window = prepare_runtime_history(session)
        starts.append(sm.history.index(window[0]) if window and window[0] in sm.history else None)
    trimmed = [s for s in starts if s]
    assert trimmed, "the budget forces trimming"
    moves = sum(1 for a, b in zip(trimmed, trimmed[1:]) if a != b)
    assert moves < len(trimmed) / 3  # far fewer prefix shifts than turns


def test_a_64k_window_returns_an_ordinary_batch_inline(tmp_path, monkeypatch):
    """Route C regression: 8 reads (~7k tokens, > 10% of a 64k window) must not be deferred
    while most of the window is free."""
    calls = []
    for i in range(8):
        path = tmp_path / f"m{i}.py"
        path.write_text("\n".join(f"def f{i}_{j}(x):\n    return x * {j}  # padding text here" for j in range(60)))
        calls.append(("read_file", {"filename": str(path)}))
    provider = _Provider(calls, window=65_536)
    _session(tmp_path, provider, monkeypatch).send_message("read them")
    sent = _second_request_text(provider)
    assert "collation buffer" not in sent and "f7_59" in sent


def test_a_deferred_read_is_not_deduplicated_later(tmp_path, monkeypatch):
    """A collated read never reached the model, so re-reading it must execute instead of
    answering "its content is in the message history above"."""
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("\n".join(f"VALUE_{i} = {i}" for i in range(400)) + "\n")
    session = _session(tmp_path, _Provider([("read_file", {"filename": str(tmp_path / "a.py")}),
                                            ("read_file", {"filename": str(tmp_path / "b.py")})], window=8_000), monkeypatch)
    session.send_message("read both")
    assert session.tool_result_cache.lookup_read_range("read_file", {"filename": str(tmp_path / "a.py")}) is None
