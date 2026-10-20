"""OpenAI Responses API path for reasoning models (provider_latency_ollama_openai.md §9.5).

Chat Completions drops a reasoning model's reasoning between calls, so every iteration of an
agent tool loop re-reasons from scratch. The Responses API with ``store=False`` and
``include=["reasoning.encrypted_content"]`` returns the reasoning as encrypted items; mucli keeps
them on the assistant's tool-call part (``thought_signature``, as Anthropic thinking is kept) and
replays them before their function calls on the next request, so the model continues its chain
of thought instead of starting over.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, Iterator, List, Optional

from .base import REPLAY_CURRENT_TURN, Message, StreamEvent, ToolDefinition, project_replay, tool_result_json

REASONING_MARKER = "openai_reasoning"


def encode_reasoning(items: List[Dict[str, Any]]) -> str:
    return json.dumps({REASONING_MARKER: items}, separators=(",", ":"))


def decode_reasoning(signature: Optional[str]) -> List[Dict[str, Any]]:
    if not signature or REASONING_MARKER not in signature:
        return []
    try:
        data = json.loads(signature)
    except ValueError:
        return []
    items = data.get(REASONING_MARKER) if isinstance(data, dict) else None
    return [i for i in items or [] if isinstance(i, dict) and i.get("type") == "reasoning"]


def _user_content(provider, message: Message) -> List[Dict[str, Any]]:
    content: List[Dict[str, Any]] = []
    for part in message.parts:
        if part.type == "text" and part.text:
            content.append({"type": "input_text", "text": part.text})
        elif part.type == "image_input" and part.image and provider.supports_input_mime(
                part.image.mime_type, part.image.display_name or ""):
            data = base64.b64encode(part.image.data).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{part.image.mime_type};base64,{data}"})
        elif part.type == "file" and part.file_ref:
            if str(part.file_ref.uri or "").startswith("file-"):
                content.append({"type": "input_file", "file_id": part.file_ref.uri})
            else:
                content.append({"type": "input_text", "text": f"[File: {part.file_ref.display_name}]"})
    return content


def build_input(provider, messages: List[Message]) -> List[Dict[str, Any]]:
    """Harness messages → Responses ``input`` items (reasoning replayed before its calls).

    Encrypted reasoning is replayed for the current user turn only: the API ignores reasoning
    items from earlier turns, so sending them is pure request size (``project_replay``)."""
    items: List[Dict[str, Any]] = []
    messages = project_replay(messages, getattr(provider, "replay_scope", REPLAY_CURRENT_TURN))
    for message in messages:
        if message.role == "tool":
            for part in message.parts:
                if part.type != "tool_result":
                    continue
                payload = part.tool_result
                if isinstance(payload, (dict, list)):
                    payload = tool_result_json(payload)
                items.append({"type": "function_call_output", "call_id": part.tool_call_id or part.tool_name or "",
                              "output": str(payload if payload is not None else "")})
            continue
        if message.role == "assistant":
            text = "".join(p.text or "" for p in message.parts if p.type == "text")
            reasoning: List[Dict[str, Any]] = []
            calls: List[Dict[str, Any]] = []
            for part in message.parts:
                if part.type != "tool_call":
                    continue
                for item in decode_reasoning(part.thought_signature):
                    if item not in reasoning:
                        reasoning.append(item)
                args = part.tool_args if isinstance(part.tool_args, dict) else {}
                calls.append({"type": "function_call", "call_id": part.tool_call_id or part.tool_name or "",
                              "name": part.tool_name or "", "arguments": json.dumps(args, sort_keys=True)})
            items.extend(reasoning)
            if text.strip():
                items.append({"role": "assistant", "content": text})
            items.extend(calls)
            continue
        content = _user_content(provider, message)
        if content:
            items.append({"role": "user", "content": content})
    return items


BREAKPOINT = {"mode": "explicit"}


def add_cache_breakpoints(items: List[Dict[str, Any]], system_prompt: Optional[str], *,
                          volatile_tail_parts: int = 0) -> List[Dict[str, Any]]:
    """GPT-5.6+ prompt caching: ``items`` with the system prompt as a leading developer message
    and explicit ``prompt_cache_breakpoint`` markers on (1) that message and (2) the last
    content block, before the volatile tail, of the latest user message — the request's stable
    end that the next request repeats. Content blocks inside input messages are the only place
    a breakpoint is accepted (not ``instructions``, not function-call items)."""
    out = [dict(item) for item in items]
    if system_prompt:
        out.insert(0, {"role": "developer",
                       "content": [{"type": "input_text", "text": system_prompt, "prompt_cache_breakpoint": dict(BREAKPOINT)}]})
    for idx in range(len(out) - 1, -1, -1):
        item = out[idx]
        if item.get("role") != "user" or not isinstance(item.get("content"), list):
            continue
        content = [dict(block) for block in item["content"]]
        # The runtime-state tail is the last part of the last user message (or the whole message).
        skip = volatile_tail_parts if idx == len(out) - 1 else 0
        stable = len(content) - skip
        if stable <= 0:
            continue
        content[stable - 1]["prompt_cache_breakpoint"] = dict(BREAKPOINT)
        out[idx] = {**item, "content": content}
        break
    return out


def convert_tools(tools: Optional[List[ToolDefinition]]) -> Optional[List[Dict[str, Any]]]:
    if not tools:
        return None
    out = []
    for tool in tools:
        schema = dict(tool.parameters or {})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        out.append({"type": "function", "name": tool.name, "description": tool.description or "",
                    "parameters": schema, "strict": False})
    return out


def _dump(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    return dump(exclude_none=True) if callable(dump) else dict(getattr(item, "__dict__", {}))


def stream_events(response_stream) -> Iterator[StreamEvent]:
    """Map a Responses event stream onto mucli ``StreamEvent``s."""
    call_ids: Dict[str, str] = {}  # item id → call id
    names: Dict[str, str] = {}
    reasoning: List[Dict[str, Any]] = []
    pending: List[StreamEvent] = []
    for event in response_stream:
        kind = getattr(event, "type", "")
        if kind == "response.output_text.delta":
            if getattr(event, "delta", ""):
                yield StreamEvent(kind="text_delta", text=event.delta)
        elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            if getattr(event, "delta", ""):
                yield StreamEvent(kind="thinking_delta", text=event.delta)
        elif kind == "response.output_item.added":
            item = _dump(event.item)
            if item.get("type") == "function_call":
                call_ids[str(item.get("id") or "")] = str(item.get("call_id") or "")
                names[str(item.get("call_id") or "")] = str(item.get("name") or "")
                yield StreamEvent(kind="tool_call_start", tool_name=item.get("name"), tool_call_id=item.get("call_id"))
        elif kind == "response.function_call_arguments.delta":
            call_id = call_ids.get(str(getattr(event, "item_id", "") or ""), "")
            if call_id and getattr(event, "delta", ""):
                yield StreamEvent(kind="tool_call_args_delta", text=event.delta, tool_call_id=call_id,
                                  tool_name=names.get(call_id))
        elif kind == "response.output_item.done":
            item = _dump(event.item)
            if item.get("type") == "reasoning":
                reasoning.append({k: v for k, v in item.items() if k in ("type", "id", "encrypted_content", "summary")})
            elif item.get("type") == "function_call":
                raw = str(item.get("arguments") or "").strip()
                try:
                    parsed = json.loads(raw) if raw else {}
                    args = parsed if isinstance(parsed, dict) else {"_value": parsed}
                except ValueError:
                    args = {"_raw": raw}
                pending.append(StreamEvent(kind="tool_call_complete", tool_name=item.get("name"), tool_args=args,
                                           tool_call_id=item.get("call_id")))
        elif kind == "response.completed":
            usage = getattr(getattr(event, "response", None), "usage", None)
            # Reasoning rides on the first tool call of this response (replayed next request).
            if reasoning and pending:
                pending[0].thought_signature = encode_reasoning(reasoning)
            yield from pending
            pending = []
            if usage is not None:
                details = getattr(usage, "input_tokens_details", None)
                cached = getattr(details, "cached_tokens", 0) or 0
                # GPT-5.6+ also report the tokens written to the cache this request.
                written = getattr(details, "cache_write_tokens", 0) or 0
                thinking = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0
                yield StreamEvent(kind="usage", input_tokens=getattr(usage, "input_tokens", 0) or 0,
                                  output_tokens=getattr(usage, "output_tokens", 0) or 0,
                                  total_tokens=getattr(usage, "total_tokens", 0) or 0,
                                  cached_tokens=cached, reasoning_tokens=thinking,
                                  cache_read_tokens=cached if written else 0,
                                  cache_creation_tokens=written if isinstance(written, int) else 0)
        elif kind == "response.incomplete":  # e.g. max output tokens: keep what arrived
            if reasoning and pending:
                pending[0].thought_signature = encode_reasoning(reasoning)
            yield from pending
            pending = []
        elif kind in ("response.failed", "error"):
            detail = getattr(getattr(event, "response", None), "error", None) or getattr(event, "message", "") or kind
            raise RuntimeError(f"OpenAI Responses stream {kind}: {detail}")
    if pending:  # stream ended without response.completed
        if reasoning:
            pending[0].thought_signature = encode_reasoning(reasoning)
        yield from pending
