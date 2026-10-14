"""Phase 1 of the harness latency plan: conversation history must be cacheable when the request
ends with mucli's request-only RUNTIME STATE block (documentation/plans/harness_latency_fixes.md)."""
import json

from providers.base import CacheHint, Message, MessagePart, ToolDefinition
from mu.session.runtime_state import RUNTIME_STATE_HEADER
from test_anthropic_provider import _provider

TOOLS = [ToolDefinition(name="read_file", description="Read", parameters={"type": "object", "properties": {"path": {"type": "string"}}})]


def runtime(n):
    return MessagePart(type="text", text=f"{RUNTIME_STATE_HEADER}\n\nclock: tick {n}")


def call(i, name="read_file"):
    return Message(role="assistant", parts=[MessagePart(type="tool_call", tool_name=name, tool_args={"path": f"f{i}.py"})])


def result(i):
    return Message(role="tool", parts=[MessagePart(type="tool_result", tool_name="read_file", tool_result=f"contents {i}")])


def history(turns):
    msgs = [Message(role="user", parts=[MessagePart(type="text", text="fix the bug")])]
    for i in range(turns):
        msgs += [call(i), result(i)]
    return msgs


def build(msgs, n, hint=True):
    wire = msgs + [Message(role="user", parts=[runtime(n)])]
    return _provider("claude-opus-5")._build_request(
        wire, "SYSTEM", False, TOOLS, CacheHint(volatile_tail_parts=1) if hint else None, None)


def marks(request):
    return [(mi, bi) for mi, msg in enumerate(request["messages"]) for bi, block in enumerate(msg["content"])
            if "cache_control" in block]


def test_volatile_tail_moves_the_breakpoint_before_runtime_state():
    request = build(history(2), 1)
    assert "cache_control" not in request  # no automatic last-block breakpoint
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    last = request["messages"][-1]["content"]
    assert last[-1]["text"].startswith(RUNTIME_STATE_HEADER) and "cache_control" not in last[-1]
    assert last[-2]["type"] == "tool_result" and last[-2]["cache_control"] == {"type": "ephemeral"}
    # Plus the previous user turn (the position the previous request wrote): at most 3 breakpoints.
    assert len(marks(request)) == 2


def test_next_request_repeats_the_marked_prefix_byte_for_byte():
    first = build(history(2), 1)
    second = build(history(3), 2)
    mi, bi = max(marks(first))  # the breakpoint just before the runtime-state tail
    prefix_first = first["messages"][:mi] + [dict(first["messages"][mi], content=first["messages"][mi]["content"][:bi + 1])]

    def strip(blocks):
        return json.loads(json.dumps(blocks).replace(', "cache_control": {"type": "ephemeral"}', ""))
    prefix_second = second["messages"][:mi] + [dict(second["messages"][mi], content=second["messages"][mi]["content"][:bi + 1])]
    assert strip(prefix_first) == strip(prefix_second)
    # The second request reads that prefix directly: its previous-turn breakpoint sits on it.
    assert (mi, bi) in marks(second)


def test_first_iteration_marks_the_user_prompt_not_runtime_state():
    msgs = [Message(role="user", parts=[MessagePart(type="text", text="hello"), runtime(0)])]
    request = _provider("claude-opus-5")._build_request(msgs, "SYSTEM", False, TOOLS, CacheHint(volatile_tail_parts=1), None)
    content = request["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in content[1]


def test_without_volatile_tail_the_automatic_breakpoint_is_unchanged():
    request = build(history(1), 1, hint=False)
    assert request["cache_control"] == {"type": "ephemeral"} and marks(request) == []


def test_loop_passes_the_hint_only_for_a_runtime_state_tail_and_capable_providers():
    from mu.agent.retry import _stream_cache_kwargs

    class Capable:
        def stream(self, messages, system_prompt=None, thinking=False, tools=None, cache_hint=None):
            pass

    class Legacy:
        def stream(self, messages, system_prompt=None, thinking=False, tools=None):
            pass
    tail = history(1) + [Message(role="user", parts=[runtime(1)])]
    assert _stream_cache_kwargs(Capable(), tail)["cache_hint"].volatile_tail_parts == 1
    assert _stream_cache_kwargs(Legacy(), tail) == {}
    assert _stream_cache_kwargs(Capable(), history(1)) == {}
