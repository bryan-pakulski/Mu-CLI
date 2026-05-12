# Abstract LLMProvider and standardized message schemas.
#
# This file defines the contract that every LLM provider implements. The
# important surfaces:
#
#   * Message / MessagePart           — provider-agnostic conversation format.
#   * ToolDefinition                  — JSON-schema description of a tool the
#                                       model may call.
#   * StreamEvent                     — single event yielded from `stream()`.
#   * CacheHint                       — opt-in prompt-caching hints; each
#                                       provider interprets in its own way.
#   * LLMProvider                     — abstract base. Subclasses implement
#                                       `generate()` (required) and may
#                                       override `stream()` to do real SSE.
#                                       Providers that override `stream()` can
#                                       have `generate()` collapse to a single
#                                       call to `drain_stream()`.
from abc import ABC, abstractmethod
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
)
from dataclasses import dataclass, field


@dataclass
class FileReference:
    uri: str
    mime_type: str
    display_name: str


@dataclass
class ImageData:
    """Inline image input for vision-capable models."""

    data: bytes
    mime_type: str  # e.g. "image/png", "image/jpeg"
    source: Optional[str] = None  # original file path or URL, for display


@dataclass
class MessagePart:
    type: str  # 'text', 'file', 'tool_call', 'tool_result', 'image_inline', 'image_input'
    text: Optional[str] = None
    file_ref: Optional[FileReference] = None
    inline_data: Optional[bytes] = None
    image: Optional[ImageData] = None

    # For agentic tool calls (Model -> User)
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    thought_signature: Optional[str] = None
    tool_call_id: Optional[str] = None  # provider-issued id; used to pair tool_call ↔ tool_result

    # For agentic tool results (User -> Model)
    tool_result: Optional[Any] = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema format
    requires_approval: bool = True


@dataclass
class Message:
    role: str  # 'user', 'assistant', 'system', 'tool'
    parts: List[MessagePart] = field(default_factory=list)


@dataclass
class CacheHint:
    """Optional hint to the provider about which prompt content should be cached.

    Provider interpretation:
      * Gemini  — used to decide whether to create/reuse an explicit Context
                  Cache entry for the system prompt + early history.
      * OpenAI  — cache is automatic on stable prefixes; this is informational
                  and used to report cache-hit telemetry back via usage.
      * Ollama  — `keep_alive_seconds` is mapped to the `keep_alive` request
                  field so the model stays resident across turns.
    """

    cache_system_prompt: bool = True
    cache_tools: bool = True
    cache_history_until_index: Optional[int] = None
    keep_alive_seconds: int = 600


@dataclass
class StreamEvent:
    """A single event yielded from `LLMProvider.stream()`.

    kinds:
      * text_delta             - partial assistant text (use `text`)
      * thinking_delta         - partial reasoning/thought (use `text`)
      * tool_call_start        - tool invocation began (use `tool_name`,
                                 `tool_call_id`)
      * tool_call_args_delta   - partial JSON chunk for tool args (use `text`)
      * tool_call_complete     - tool args finalized (use `tool_name`,
                                 `tool_args`, `tool_call_id`,
                                 optional `thought_signature`)
      * usage                  - token accounting (uses the int counters)
      * error                  - stream-side error (use `text` for message)
      * done                   - end of stream
    """

    kind: str
    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    thought_signature: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class ProviderResponse:
    text: str
    parts: List[MessagePart]
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0


class LLMProvider(ABC):
    def __init__(self, model_name: str = ""):
        self.name = ""
        self.model_name = model_name

    @abstractmethod
    def get_available_models(self) -> List[str]:
        """Returns a list of available model names for this provider."""

    def effective_context_window(
        self, model_name: Optional[str] = None
    ) -> Optional[int]:
        """Return the real input-context ceiling (in tokens) for the active
        model, or `None` if the provider can't determine it.

        The session compactor uses this to set its rolling budget. Returning
        `None` falls back to the user-set `context_token_limit` variable —
        which is fine for frontier API providers (Claude/Gemini/OpenAI) but
        wrong for Ollama, where every model has a much smaller real window
        (often 4k-32k) than the harness default of 256k.

        Implementations should cache the result per model_name since this is
        called once per turn.
        """
        return None

    @abstractmethod
    def generate(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        thinking: bool = False,
        tools: Optional[List[ToolDefinition]] = None,
    ) -> ProviderResponse:
        """Send the standardized conversation history to the LLM (non-streaming)."""

    def stream(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        thinking: bool = False,
        tools: Optional[List[ToolDefinition]] = None,
        cache_hint: Optional[CacheHint] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Iterator[StreamEvent]:
        """Stream events as they arrive.

        Default implementation synthesizes events from `generate()` so any
        provider (including the fakes used in tests) gets a working stream
        for free. Real providers override this to yield deltas in real time.
        Subclasses that do so should also have `generate()` simply drain
        `stream()` via `drain_stream()`.

        The `cache_hint` and `reasoning_effort` kwargs are not forwarded to
        `generate()` here because most legacy subclasses do not accept them.
        Real streaming providers override this method and honor them directly.
        """

        response = self.generate(
            messages=messages,
            system_prompt=system_prompt,
            thinking=thinking,
            tools=tools,
        )
        if response.text:
            yield StreamEvent(kind="text_delta", text=response.text)
        # The default wrapper has the full tool call up front, so it emits only
        # `tool_call_complete`. Real streaming providers emit both
        # `tool_call_start` and `tool_call_complete` so live UIs can show the
        # call before its args have finished arriving.
        for part in response.parts:
            if part.type == "tool_call":
                yield StreamEvent(
                    kind="tool_call_complete",
                    tool_name=part.tool_name,
                    tool_args=part.tool_args or {},
                    thought_signature=part.thought_signature,
                    tool_call_id=part.tool_call_id,
                )
        yield StreamEvent(
            kind="usage",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            total_tokens=response.total_tokens,
            cached_tokens=response.cached_tokens,
            reasoning_tokens=response.reasoning_tokens,
        )
        yield StreamEvent(kind="done")

    def drain_stream(self, events: Iterable[StreamEvent]) -> ProviderResponse:
        """Accumulate stream events into a ProviderResponse.

        Streaming providers can use this to implement `generate()`:

            def generate(self, messages, ...):
                return self.drain_stream(self.stream(messages, ...))

        Tool-call delta chunks (`tool_call_args_delta`) are joined and parsed
        as JSON when `tool_call_complete` does not provide a finalized args
        dict. Out-of-order events are tolerated; final parts are emitted in
        the order tool calls first appeared.
        """

        import json as _json

        text_buf: List[str] = []
        ordered_call_ids: List[str] = []
        partial_calls: Dict[str, Dict[str, Any]] = {}
        in_tok = out_tok = tot_tok = cached_tok = reasoning_tok = 0

        for ev in events:
            if ev.kind == "text_delta" and ev.text:
                text_buf.append(ev.text)
            elif ev.kind == "tool_call_start":
                cid = ev.tool_call_id or f"call_{len(ordered_call_ids)}"
                if cid not in partial_calls:
                    partial_calls[cid] = {
                        "name": ev.tool_name,
                        "args_chunks": [],
                        "args": None,
                        "signature": ev.thought_signature,
                    }
                    ordered_call_ids.append(cid)
                else:
                    if ev.tool_name and not partial_calls[cid]["name"]:
                        partial_calls[cid]["name"] = ev.tool_name
            elif ev.kind == "tool_call_args_delta":
                cid = ev.tool_call_id
                if cid is None:
                    if ordered_call_ids:
                        cid = ordered_call_ids[-1]
                    else:
                        continue
                if cid not in partial_calls:
                    partial_calls[cid] = {
                        "name": ev.tool_name,
                        "args_chunks": [],
                        "args": None,
                        "signature": ev.thought_signature,
                    }
                    ordered_call_ids.append(cid)
                if ev.text:
                    partial_calls[cid]["args_chunks"].append(ev.text)
                if ev.tool_name and not partial_calls[cid]["name"]:
                    partial_calls[cid]["name"] = ev.tool_name
            elif ev.kind == "tool_call_complete":
                cid = ev.tool_call_id or f"call_{len(ordered_call_ids)}"
                if cid not in partial_calls:
                    partial_calls[cid] = {
                        "name": ev.tool_name,
                        "args_chunks": [],
                        "args": ev.tool_args or {},
                        "signature": ev.thought_signature,
                    }
                    ordered_call_ids.append(cid)
                else:
                    if ev.tool_args is not None:
                        partial_calls[cid]["args"] = ev.tool_args
                    if ev.tool_name:
                        partial_calls[cid]["name"] = ev.tool_name
                    if ev.thought_signature:
                        partial_calls[cid]["signature"] = ev.thought_signature
            elif ev.kind == "usage":
                in_tok = ev.input_tokens or in_tok
                out_tok = ev.output_tokens or out_tok
                tot_tok = ev.total_tokens or tot_tok
                cached_tok = ev.cached_tokens or cached_tok
                reasoning_tok = ev.reasoning_tokens or reasoning_tok
            # 'error' and 'done' are signalling-only here. Callers wanting
            # to react to them should consume the stream directly.

        text = "".join(text_buf)
        parts: List[MessagePart] = []
        if text:
            parts.append(MessagePart(type="text", text=text))
        for cid in ordered_call_ids:
            call = partial_calls[cid]
            args = call["args"]
            if args is None:
                joined = "".join(call["args_chunks"]).strip()
                if joined:
                    try:
                        args = _json.loads(joined)
                    except Exception:
                        args = {"_raw": joined}
                else:
                    args = {}
            parts.append(
                MessagePart(
                    type="tool_call",
                    tool_name=call["name"],
                    tool_args=args if isinstance(args, dict) else {"_value": args},
                    thought_signature=call["signature"],
                    tool_call_id=cid,
                )
            )
        return ProviderResponse(
            text=text,
            parts=parts,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=tot_tok or (in_tok + out_tok),
            cached_tokens=cached_tok,
            reasoning_tokens=reasoning_tok,
        )

    @abstractmethod
    def upload_file(self, file_path: str, mime_type: str) -> Optional[FileReference]:
        """Upload a file to the provider's storage mechanism (if required)."""


__all__ = [
    "CacheHint",
    "FileReference",
    "ImageData",
    "LLMProvider",
    "Message",
    "MessagePart",
    "ProviderResponse",
    "StreamEvent",
    "ToolDefinition",
]
