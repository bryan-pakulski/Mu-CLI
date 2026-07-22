"""Action records in compaction (spec #4/#5)."""

from mu.session.action_record import is_action_record_eligible, render_action_record
from mu.session.history import HistoryMixin
from mu.session.messages import summarize_message_parts


def _part(*, tool_name="read_file", env=None, cache_key=None, raw=None, tool_args=None):
    tr = env if env is not None else raw
    return {
        "type": "tool_result",
        "tool_name": tool_name,
        "tool_result": tr,
        "cache_key": cache_key,
        "tool_args": tool_args or {},
    }


def test_render_preserves_decision_and_cache_key():
    env = {
        "tool_name": "read_file",
        "ok": True,
        "summary": "read 2341 bytes",
        "args": "filename=src/foo.py",
        "error_code": None,
        "modified_files": ["src/foo.py"],
    }
    rec = render_action_record(_part(env=env, cache_key="a1b2c3d4e5f6"))
    assert "action: read_file" in rec
    assert "filename=src/foo.py" in rec
    assert "ok=true" in rec
    assert "files=src/foo.py" in rec
    assert "[cache:a1b2c3d4e5f6]" in rec
    assert "outcome=read 2341 bytes" in rec


def test_render_preserves_unresolved_error_code():
    env = {
        "tool_name": "bash",
        "ok": False,
        "summary": "build failed",
        "args": "command=make",
        "error_code": "non_zero_exit",
        "modified_files": [],
    }
    rec = render_action_record(_part(env=env, cache_key="k"))
    assert "ok=false" in rec
    assert "errors=non_zero_exit" in rec


def test_render_drops_raw_output():
    env = {
        "tool_name": "bash",
        "ok": True,
        "summary": "built",
        "args": "command=make",
        "error_code": None,
        "modified_files": [],
        "raw": "X" * 50000,  # full raw must NOT appear in the record
    }
    rec = render_action_record(_part(env=env, cache_key="k"))
    assert "X" * 100 not in rec
    assert len(rec) < 500


def test_eligibility_requires_cache_key_or_envelope():
    assert is_action_record_eligible(_part(raw="plain string", cache_key="k"))
    assert is_action_record_eligible(_part(env={"ok": True}))
    # Raw string with no cache_key and no envelope → legacy prose.
    assert not is_action_record_eligible(_part(raw="plain string"))


def test_summarize_message_parts_uses_action_record():
    env = {
        "tool_name": "read_file",
        "ok": True,
        "summary": "read foo",
        "args": "filename=foo.py",
        "error_code": None,
        "modified_files": ["foo.py"],
    }
    msg = {"role": "tool", "parts": [_part(env=env, cache_key="KEY1")]}
    line = summarize_message_parts(msg)
    assert "action: read_file" in line
    assert "[cache:KEY1]" in line
    # The full raw must not be replayed.
    assert "raw" not in line.lower() or "raw" not in line


def test_summarize_message_parts_legacy_for_raw_without_cache_key():
    msg = {"role": "tool", "parts": [_part(raw="plain output line")]}
    line = summarize_message_parts(msg)
    assert "tool_result:" in line
    assert "plain output line" in line


def test_render_entries_uses_action_record_for_envelope():
    # HistoryMixin._render_entries_for_llm
    env = {
        "tool_name": "search_for_string",
        "ok": True,
        "summary": "3 matches",
        "args": "string=foo",
        "error_code": None,
        "modified_files": [],
    }
    entries = [{"role": "tool", "parts": [_part(env=env, cache_key="CK")]}]
    out = HistoryMixin._render_entries_for_llm(None, entries)
    assert "action: search_for_string" in out
    assert "[cache:CK]" in out


def test_render_entries_legacy_for_raw_string():
    entries = [
        {"role": "tool", "parts": [_part(raw="some raw tool output " * 100)]}
    ]
    out = HistoryMixin._render_entries_for_llm(None, entries)
    assert "tool_result:" in out
    # Legacy prose path keeps a cache_tag only if cache_key present; here none.
    assert "[cache:" not in out