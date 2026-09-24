# Anthropic (Claude) provider with real streaming via the official
# `anthropic` Python SDK (Messages API), prompt caching, adaptive thinking /
# effort control, tool use, and vision / document input.
#
# Notable harness-integration details:
#
#   * Tool-call pairing. The harness persists tool calls and tool results
#     WITHOUT provider ids (see mu/session/messages.py). Claude requires every
#     `tool_use.id` to be answered by a `tool_result` with the identical id in
#     the very next user turn, so `_convert_messages()` synthesizes
#     deterministic ids (`mucli_toolu_N`) from message order. Deterministic ids
#     keep the request prefix byte-stable between iterations, which is what
#     makes prompt caching hit.
#
#   * Thinking blocks. With thinking enabled, an assistant turn whose
#     `tool_use` is still waiting on its `tool_result` must be replayed with
#     its `thinking` blocks intact (signature-verified). We serialize the
#     thinking blocks that preceded each tool call into the tool-call part's
#     `thought_signature` (the same slot Gemini uses), which the harness
#     persists and copies onto the matching tool_result part. On replay only
#     the LAST assistant turn with tool calls gets its thinking blocks back;
#     earlier turns are sent as text + tool_use only. That is the shape the
#     API docs recommend for harnesses that compact / edit history client-side
#     (older thinking blocks may be invalidated by those edits on
#     preserved-thinking models). If the API still rejects a replayed block,
#     the request is retried once without thinking replay.
#
#   * Prompt caching. One explicit breakpoint on the system prompt (the
#     stable prefix) plus top-level automatic caching for the growing
#     conversation tail. Set ANTHROPIC_AUTO_CACHE=0 when talking to a proxy or
#     platform that rejects the top-level `cache_control` field.
import base64
import json
import os
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

import anthropic
from anthropic import Anthropic

from .base import (
    CacheHint,
    FileReference,
    LLMProvider,
    MediaData,
    Message,
    MessagePart,
    ProviderResponse,
    StreamEvent,
    ToolDefinition,
)
from utils.model_pricing import input_modality_for_mime


DEFAULT_MODEL = "claude-opus-5"

# Used when the Models API is unreachable (offline, restricted key, proxy).
_FALLBACK_MODELS = [
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]

_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_PDF_MIME = "application/pdf"
_TEXT_DOCUMENT_MIMES = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/rtf",
}
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
_THINKING_MARKER = "anthropic_thinking"

# `claude-opus-5`, `claude-fable-5-1`, `claude-3-7-sonnet-20250219`,
# `claude-sonnet-4-20250514`, `anthropic.claude-opus-4-6`, ...
_VERSION_RE = re.compile(r"claude-(?:[a-z]+-)*?(\d+)(?:[-.](\d+))?")


def _sanitize_stream_error(exc: BaseException, *, cap: int = 300) -> str:
    """Bound and scrub an SDK exception message before it reaches a
    StreamEvent. SDK exceptions can embed upstream response bodies, request
    URLs, and prompt fragments; events are displayed/logged/traced, so the
    text is secret-redacted and length-capped. The exception class name is
    always preserved so callers can still distinguish error categories."""
    try:
        from mu.security.secret_paths import redact_secrets

        text, _ = redact_secrets(str(exc))
    except Exception:
        text = str(exc)
    text = " ".join(text.split())
    if len(text) > cap:
        text = text[: cap - 3].rstrip() + "..."
    return f"{type(exc).__name__}: {text}"


# ------------------------------------------------------------ model metadata


def _model_version(model_name: str) -> Tuple[int, int]:
    """Return (major, minor) parsed from a Claude model id, or (0, 0)."""
    name = str(model_name or "").lower().replace("models/", "")
    idx = name.find("claude-")
    if idx < 0:
        return (0, 0)
    match = _VERSION_RE.search(name[idx:])
    if not match:
        return (0, 0)
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    if minor >= 100:  # date suffix such as claude-sonnet-4-20250514
        minor = 0
    return (major, minor)


def _thinking_mode(model_name: str) -> str:
    """'adaptive' (4.6+), 'budget' (3.7 - 4.5), or 'none'."""
    version = _model_version(model_name)
    if version >= (4, 6):
        return "adaptive"
    if version >= (3, 7):
        return "budget"
    return "none"


def _thinking_on_by_default(model_name: str) -> bool:
    """Opus 5+, Fable and Mythos run adaptive thinking even when the
    `thinking` parameter is omitted."""
    return _model_version(model_name) >= (5, 0)


def _default_max_tokens(model_name: str) -> int:
    version = _model_version(model_name)
    if version >= (4, 6):
        return 64000
    if version >= (3, 7):
        return 32000
    return 8192


def _normalise_effort(effort: Optional[str], model_name: str) -> Optional[str]:
    value = str(effort or "").strip().lower()
    if value not in _EFFORT_LEVELS:
        return None
    if value == "xhigh" and _model_version(model_name) < (4, 7):
        return "high"  # xhigh arrived with Opus 4.7
    return value


def has_ambient_credentials() -> bool:
    """True when the SDK can resolve credentials without an explicit key:
    env vars, a workload-identity rule, or an `ant auth login` profile."""
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_PROFILE",
        "ANTHROPIC_FEDERATION_RULE_ID",
    ):
        if os.environ.get(key):
            return True
    config_root = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.isdir(os.path.join(config_root, "anthropic"))


# ---------------------------------------------------------- content helpers


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _media_block(media: MediaData) -> Optional[Dict[str, Any]]:
    """Serialize native media as a Messages API content block, or None when
    Claude has no input shape for it (audio, video, unknown binaries)."""
    mime_type = str(media.mime_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    title = media.display_name or None
    if mime_type in _IMAGE_MIMES:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": _b64(media.data)},
        }
    if mime_type == _PDF_MIME:
        block: Dict[str, Any] = {
            "type": "document",
            "source": {"type": "base64", "media_type": _PDF_MIME, "data": _b64(media.data)},
        }
        if title:
            block["title"] = title
        return block
    if mime_type.startswith("text/") or mime_type in _TEXT_DOCUMENT_MIMES:
        text = media.data.decode("utf-8", errors="replace")
        if not text.strip():
            return None
        block = {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": text},
        }
        if title:
            block["title"] = title
        return block
    return None


def _encode_thinking(blocks: List[Dict[str, Any]]) -> Optional[str]:
    if not blocks:
        return None
    return json.dumps({_THINKING_MARKER: blocks}, sort_keys=True)


def _decode_thinking(signature: Optional[str]) -> List[Dict[str, Any]]:
    """Return the thinking blocks serialized by `_encode_thinking`, or []
    for anything else (Gemini hex signatures, garbage, None)."""
    if not signature or not str(signature).startswith("{"):
        return []
    try:
        payload = json.loads(signature)
    except (TypeError, ValueError):
        return []
    blocks = payload.get(_THINKING_MARKER) if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return []
    out: List[Dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "thinking" and block.get("signature"):
            out.append(
                {
                    "type": "thinking",
                    "thinking": str(block.get("thinking") or ""),
                    "signature": str(block["signature"]),
                }
            )
        elif block.get("type") == "redacted_thinking" and block.get("data"):
            out.append({"type": "redacted_thinking", "data": str(block["data"])})
    return out


def _parse_tool_input(joined: str, snapshot: Any) -> Dict[str, Any]:
    text = (joined or "").strip()
    if text:
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"_value": parsed}
        except json.JSONDecodeError:
            pass
    if isinstance(snapshot, dict) and snapshot:
        return snapshot
    if text:
        return {"_raw": text}
    return {}


class AnthropicProvider(LLMProvider):
    """Provider for Anthropic Claude models (Messages API)."""

    # Harness-controlled HTTP timeout (seconds). Single Claude turns on hard
    # tasks can legitimately run for many minutes; streaming keeps the
    # connection alive, this is the ceiling for one request.
    DEFAULT_TIMEOUT_SECONDS = 600.0

    def __init__(
        self,
        model_name: str = "",
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ):
        if not api_key and not has_ambient_credentials():
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is required. "
                "Set it via: export ANTHROPIC_API_KEY='your-key' "
                "(or authenticate once with `ant auth login`)."
            )
        super().__init__(model_name)
        self.name = "anthropic"
        try:
            env_timeout = float(os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", "") or 0)
        except ValueError:
            env_timeout = 0.0
        self.timeout_seconds = float(
            timeout_seconds or env_timeout or self.DEFAULT_TIMEOUT_SECONDS
        )
        client_kwargs: Dict[str, Any] = {"timeout": self.timeout_seconds}
        if api_key:
            client_kwargs["api_key"] = api_key
        # ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / profiles are resolved
        # by the SDK itself.
        self._client = Anthropic(**client_kwargs)
        self._model_info_cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------ models

    def get_available_models(self) -> List[str]:
        try:
            ids = [
                str(getattr(m, "id", "") or "")
                for m in self._client.models.list()
            ]
            models = [m for m in ids if m.startswith("claude")]
            return models or list(_FALLBACK_MODELS)
        except Exception:
            return list(_FALLBACK_MODELS)

    def _model_info(self, model_name: str) -> Any:
        """Cached `client.models.retrieve()` result (or None on failure)."""
        key = str(model_name or "")
        if key in self._model_info_cache:
            return self._model_info_cache[key]
        info = None
        try:
            info = self._client.models.retrieve(key)
        except Exception:
            info = None
        self._model_info_cache[key] = info
        return info

    def _resolve_max_tokens(self, model_name: str) -> int:
        env_value = os.environ.get("ANTHROPIC_MAX_TOKENS", "").strip()
        if env_value:
            try:
                configured = int(env_value)
                if configured > 0:
                    return configured
            except ValueError:
                pass
        limit = _default_max_tokens(model_name)
        info = self._model_info(model_name)
        cap = getattr(info, "max_tokens", None) if info is not None else None
        if isinstance(cap, int) and cap > 0:
            limit = min(limit, cap)
        return limit

    def effective_context_window(
        self, model_name: Optional[str] = None
    ) -> Optional[int]:
        model = model_name or self.model_name or DEFAULT_MODEL
        info = self._model_info(model)
        window = getattr(info, "max_input_tokens", None) if info is not None else None
        if isinstance(window, int) and window > 0:
            return window
        try:
            from utils.model_pricing import resolve_token_pricing

            pricing = resolve_token_pricing(self.name, model)
            if pricing is not None and pricing.context_window:
                return int(pricing.context_window)
        except Exception:
            pass
        return None

    def effective_response_reserve(
        self, model_name: Optional[str] = None
    ) -> Optional[int]:
        return self._resolve_max_tokens(model_name or self.model_name or DEFAULT_MODEL)

    # -------------------------------------------------------------- modalities

    def supports_input_mime(self, mime_type: str, filename: str = "") -> bool:
        if not super().supports_input_mime(mime_type, filename):
            return False
        mime = str(mime_type or "").split(";", 1)[0].strip().lower()
        modality = input_modality_for_mime(mime, filename)
        if modality == "image":
            return mime in _IMAGE_MIMES
        if modality == "document":
            return (
                mime == _PDF_MIME
                or mime.startswith("text/")
                or mime in _TEXT_DOCUMENT_MIMES
            )
        # The Messages API has no audio or video input shape.
        return modality == "text"

    def native_media_request_limit(self) -> int:
        # Anthropic documents a 32 MB request-size limit for the Messages API.
        return 32 * 1024 * 1024

    # ------------------------------------------------------- message conversion

    @staticmethod
    def _append(out: List[Dict[str, Any]], role: str, blocks: List[Dict[str, Any]]) -> None:
        """Append blocks, merging into the previous same-role message. When
        merging tool_result blocks into an existing user turn they are placed
        before any other content (the API requires tool results first)."""
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            existing = out[-1]["content"]
            if role == "user" and blocks[0].get("type") == "tool_result":
                insert_at = 0
                for pos, block in enumerate(existing):
                    if block.get("type") == "tool_result":
                        insert_at = pos + 1
                existing[insert_at:insert_at] = blocks
            else:
                existing.extend(blocks)
            return
        out.append({"role": role, "content": list(blocks)})

    def _user_blocks(self, msg: Message) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for part in msg.parts:
            if part.type == "text":
                if part.text and part.text.strip():
                    blocks.append({"type": "text", "text": part.text})
            elif (
                part.type == "image_input"
                and part.image
                and self.supports_input_mime(
                    part.image.mime_type, part.image.display_name or ""
                )
            ):
                block = _media_block(part.image)
                if block is not None:
                    blocks.append(block)
            elif part.type == "media_input" and part.media:
                block = _media_block(part.media)
                if block is not None:
                    blocks.append(block)
            elif part.type == "file" and part.file_ref:
                uri = str(part.file_ref.uri or "")
                if uri.startswith("file_"):
                    blocks.append(
                        {
                            "type": "document",
                            "source": {"type": "file", "file_id": uri},
                            "title": part.file_ref.display_name or uri,
                        }
                    )
                else:
                    blocks.append(
                        {"type": "text", "text": f"[File: {part.file_ref.display_name}]"}
                    )
        return blocks

    def _convert_messages(
        self, messages: List[Message], *, replay_thinking: bool = True
    ) -> List[Dict[str, Any]]:
        """Convert harness messages to Messages API `messages`.

        * user / system history -> user turns (text, image, document blocks)
        * assistant             -> [thinking...] text tool_use blocks
        * tool                  -> user turn of tool_result blocks whose ids
                                   match the preceding assistant tool_use ids
        """
        out: List[Dict[str, Any]] = []
        counter = 0
        pending: List[Tuple[str, str]] = []  # tool_use ids awaiting results

        last_tool_turn = -1
        for idx, msg in enumerate(messages):
            if msg.role == "assistant" and any(p.type == "tool_call" for p in msg.parts):
                last_tool_turn = idx

        def flush_pending() -> None:
            if not pending:
                return
            blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": cid,
                    "is_error": True,
                    "content": (
                        f"No result was returned for tool call '{name}' "
                        "(the call was interrupted before it completed)."
                    ),
                }
                for cid, name in pending
            ]
            pending.clear()
            self._append(out, "user", blocks)

        for idx, msg in enumerate(messages):
            if msg.role == "tool":
                result_blocks: List[Dict[str, Any]] = []
                orphan_blocks: List[Dict[str, Any]] = []
                for part in msg.parts:
                    if part.type != "tool_result":
                        continue
                    payload = part.tool_result
                    if isinstance(payload, (dict, list)):
                        payload = json.dumps(payload, indent=2, sort_keys=True)
                    text = str(payload if payload is not None else "")
                    media_blocks = [
                        block
                        for block in (_media_block(m) for m in (part.media_inputs or []))
                        if block is not None
                    ]
                    if pending:
                        cid, _name = pending.pop(0)
                        content: Any
                        if media_blocks:
                            content = [{"type": "text", "text": text or "(no text output)"}]
                            content.extend(media_blocks)
                        else:
                            content = text
                        result_blocks.append(
                            {"type": "tool_result", "tool_use_id": cid, "content": content}
                        )
                    else:
                        # Orphan result (its tool_use was compacted away):
                        # a tool_result with an unknown id is rejected, so
                        # carry it as plain user text instead.
                        orphan_blocks.append(
                            {
                                "type": "text",
                                "text": f"[Result of tool '{part.tool_name or 'tool'}']\n{text}",
                            }
                        )
                        orphan_blocks.extend(media_blocks)
                self._append(out, "user", result_blocks)
                self._append(out, "user", orphan_blocks)
                continue

            # Any non-tool turn closes an open tool round.
            flush_pending()

            if msg.role == "assistant":
                blocks: List[Dict[str, Any]] = []
                tool_blocks: List[Dict[str, Any]] = []
                leading_thinking: List[Dict[str, Any]] = []
                for part in msg.parts:
                    if part.type == "text":
                        if part.text and part.text.strip():
                            blocks.append({"type": "text", "text": part.text})
                    elif part.type == "tool_call":
                        counter += 1
                        cid = f"mucli_toolu_{counter}"
                        thinking_blocks = (
                            _decode_thinking(part.thought_signature)
                            if replay_thinking and idx == last_tool_turn
                            else []
                        )
                        if thinking_blocks:
                            if tool_blocks:
                                # Interleaved thinking: keep it next to the
                                # tool call it preceded.
                                tool_blocks.extend(thinking_blocks)
                            else:
                                leading_thinking.extend(thinking_blocks)
                        args = part.tool_args if isinstance(part.tool_args, dict) else {}
                        tool_blocks.append(
                            {
                                "type": "tool_use",
                                "id": cid,
                                "name": part.tool_name or "",
                                "input": args,
                            }
                        )
                        pending.append((cid, part.tool_name or ""))
                self._append(out, "assistant", leading_thinking + blocks + tool_blocks)
                continue

            # user / system / anything else -> user turn
            self._append(out, "user", self._user_blocks(msg))

        flush_pending()

        if out and out[0]["role"] != "user":
            out.insert(
                0,
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "[Earlier conversation context was omitted.]"}],
                },
            )
        return out

    def _convert_tools(
        self, tools: Optional[List[ToolDefinition]]
    ) -> Optional[List[Dict[str, Any]]]:
        if not tools:
            return None
        eager = os.environ.get("ANTHROPIC_EAGER_TOOL_STREAMING", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        out: List[Dict[str, Any]] = []
        for t in tools:
            schema = dict(t.parameters or {})
            schema.setdefault("type", "object")
            schema.setdefault("properties", {})
            entry: Dict[str, Any] = {
                "name": t.name,
                "description": t.description or "",
                "input_schema": schema,
            }
            if eager:
                entry["eager_input_streaming"] = True
            out.append(entry)
        return out

    # ----------------------------------------------------------- request build

    def _build_request(
        self,
        messages: List[Message],
        system_prompt: Optional[str],
        thinking: bool,
        tools: Optional[List[ToolDefinition]],
        cache_hint: Optional[CacheHint],
        reasoning_effort: Optional[str],
        *,
        replay_thinking: bool = True,
    ) -> Dict[str, Any]:
        model = self.model_name or DEFAULT_MODEL
        mode = _thinking_mode(model)
        max_tokens = self._resolve_max_tokens(model)

        thinking_param: Optional[Dict[str, Any]] = None
        output_config: Dict[str, Any] = {}
        effort = _normalise_effort(reasoning_effort, model) if mode == "adaptive" else None
        want_thinking = bool(thinking) or effort is not None
        if mode == "adaptive" and want_thinking:
            thinking_param = {"type": "adaptive", "display": "summarized"}
            if effort:
                output_config["effort"] = effort
        elif mode == "budget" and want_thinking:
            budget = max(1024, min(16000, max_tokens // 2))
            thinking_param = {"type": "enabled", "budget_tokens": budget}

        thinking_active = thinking_param is not None or _thinking_on_by_default(model)

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._convert_messages(
                messages, replay_thinking=replay_thinking and thinking_active
            ),
        }

        cache_system = cache_hint is None or cache_hint.cache_system_prompt
        cache_tail = (
            cache_hint is None or cache_hint.cache_system_prompt or cache_hint.cache_tools
        ) and os.environ.get("ANTHROPIC_AUTO_CACHE", "1").strip().lower() not in {
            "0", "false", "no", "off",
        }

        if system_prompt:
            block: Dict[str, Any] = {"type": "text", "text": system_prompt}
            if cache_system:
                block["cache_control"] = {"type": "ephemeral"}
            payload["system"] = [block]

        tool_defs = self._convert_tools(tools)
        if tool_defs:
            payload["tools"] = tool_defs
            payload["tool_choice"] = {"type": "auto"}

        if thinking_param is not None:
            payload["thinking"] = thinking_param
        if output_config:
            payload["output_config"] = output_config
        if cache_tail:
            # Automatic breakpoint on the last cacheable block: the whole
            # prefix built this iteration is what the next iteration reads.
            payload["cache_control"] = {"type": "ephemeral"}
        return payload

    @staticmethod
    def _is_thinking_replay_error(exc: BaseException) -> bool:
        text = str(exc).lower()
        return "thinking" in text or "signature" in text

    # --------------------------------------------------------------- streaming

    def stream(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        thinking: bool = False,
        tools: Optional[List[ToolDefinition]] = None,
        cache_hint: Optional[CacheHint] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Iterator[StreamEvent]:
        request = self._build_request(
            messages, system_prompt, thinking, tools, cache_hint, reasoning_effort
        )

        # Per content-block streaming state, keyed by block index.
        blocks: Dict[int, Dict[str, Any]] = {}
        pending_thinking: List[Dict[str, Any]] = []
        input_tokens = cache_read = cache_create = output_tokens = 0
        thinking_tokens = 0
        stop_reason: Optional[str] = None
        stop_details: Any = None
        stream_obj = None

        try:
            try:
                stream_obj = self._client.messages.stream(**request).__enter__()
            except anthropic.BadRequestError as exc:
                if not self._is_thinking_replay_error(exc):
                    raise
                # A replayed thinking block was rejected (history edited
                # between the tool call and its result). Retry once without
                # thinking replay - text and tool_use blocks are kept.
                request = self._build_request(
                    messages,
                    system_prompt,
                    thinking,
                    tools,
                    cache_hint,
                    reasoning_effort,
                    replay_thinking=False,
                )
                stream_obj = self._client.messages.stream(**request).__enter__()

            for event in stream_obj:
                et = getattr(event, "type", "")

                if et == "message_start":
                    usage = getattr(event.message, "usage", None)
                    if usage is not None:
                        input_tokens = getattr(usage, "input_tokens", 0) or 0
                        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0

                elif et == "content_block_start":
                    cb = event.content_block
                    state = {
                        "type": getattr(cb, "type", ""),
                        "id": getattr(cb, "id", None),
                        "name": getattr(cb, "name", None),
                        "json": [],
                        "thinking": [],
                        "signature": "",
                        "data": getattr(cb, "data", None),
                    }
                    blocks[event.index] = state
                    if state["type"] == "tool_use":
                        yield StreamEvent(
                            kind="tool_call_start",
                            tool_name=state["name"],
                            tool_call_id=state["id"],
                        )

                elif et == "content_block_delta":
                    delta = event.delta
                    dt = getattr(delta, "type", "")
                    state = blocks.get(event.index)
                    if dt == "text_delta":
                        if delta.text:
                            yield StreamEvent(kind="text_delta", text=delta.text)
                    elif dt == "thinking_delta":
                        if state is not None:
                            state["thinking"].append(delta.thinking or "")
                        if delta.thinking:
                            yield StreamEvent(kind="thinking_delta", text=delta.thinking)
                    elif dt == "signature_delta":
                        if state is not None:
                            state["signature"] += delta.signature or ""
                    elif dt == "input_json_delta":
                        chunk = delta.partial_json or ""
                        if state is not None:
                            state["json"].append(chunk)
                        if chunk:
                            yield StreamEvent(
                                kind="tool_call_args_delta",
                                text=chunk,
                                tool_call_id=state["id"] if state else None,
                                tool_name=state["name"] if state else None,
                            )

                elif et == "content_block_stop":
                    state = blocks.pop(event.index, None)
                    if state is None:
                        continue
                    if state["type"] == "thinking":
                        if state["signature"]:
                            pending_thinking.append(
                                {
                                    "type": "thinking",
                                    "thinking": "".join(state["thinking"]),
                                    "signature": state["signature"],
                                }
                            )
                    elif state["type"] == "redacted_thinking":
                        if state["data"]:
                            pending_thinking.append(
                                {"type": "redacted_thinking", "data": state["data"]}
                            )
                    elif state["type"] == "tool_use":
                        snapshot = getattr(
                            getattr(event, "content_block", None), "input", None
                        )
                        args = _parse_tool_input("".join(state["json"]), snapshot)
                        signature = _encode_thinking(pending_thinking)
                        pending_thinking = []
                        yield StreamEvent(
                            kind="tool_call_complete",
                            tool_name=state["name"],
                            tool_args=args,
                            tool_call_id=state["id"],
                            thought_signature=signature,
                        )

                elif et == "message_delta":
                    delta = getattr(event, "delta", None)
                    if delta is not None and getattr(delta, "stop_reason", None):
                        stop_reason = delta.stop_reason
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        output_tokens = getattr(usage, "output_tokens", 0) or output_tokens
                        if getattr(usage, "input_tokens", None) is not None:
                            input_tokens = usage.input_tokens or input_tokens
                        if getattr(usage, "cache_read_input_tokens", None) is not None:
                            cache_read = usage.cache_read_input_tokens or cache_read
                        if getattr(usage, "cache_creation_input_tokens", None) is not None:
                            cache_create = usage.cache_creation_input_tokens or cache_create
                        details = getattr(usage, "output_tokens_details", None)
                        if details is not None:
                            thinking_tokens = getattr(details, "thinking_tokens", 0) or 0

            # Authoritative final message (usage / stop details), best effort.
            try:
                final = stream_obj.get_final_message()
            except Exception:
                final = None
            if final is not None:
                stop_reason = getattr(final, "stop_reason", None) or stop_reason
                stop_details = getattr(final, "stop_details", None)
                usage = getattr(final, "usage", None)
                if usage is not None:
                    input_tokens = getattr(usage, "input_tokens", 0) or input_tokens
                    cache_read = getattr(usage, "cache_read_input_tokens", 0) or cache_read
                    cache_create = (
                        getattr(usage, "cache_creation_input_tokens", 0) or cache_create
                    )
                    output_tokens = getattr(usage, "output_tokens", 0) or output_tokens
        except Exception as exc:
            yield StreamEvent(kind="error", text=_sanitize_stream_error(exc))
            raise
        finally:
            closer = getattr(stream_obj, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass

        if stop_reason == "refusal":
            category = getattr(stop_details, "category", None)
            explanation = getattr(stop_details, "explanation", None)
            note = "[Claude declined to continue this request"
            if category:
                note += f" (category: {category})"
            note += "."
            if explanation:
                note += f" {explanation}"
            note += "]"
            yield StreamEvent(kind="text_delta", text=note)

        # Anthropic reports uncached and cached prompt tokens separately;
        # the harness expects `input_tokens` to be the full prompt with
        # `cached_tokens` as the cached subset (see model_pricing tests).
        total_input = input_tokens + cache_read + cache_create
        yield StreamEvent(
            kind="usage",
            input_tokens=total_input,
            output_tokens=output_tokens,
            total_tokens=total_input + output_tokens,
            cached_tokens=cache_read,
            reasoning_tokens=thinking_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_create,
        )
        yield StreamEvent(kind="done")

    # ----------------------------------------------------- non-streaming path

    def generate(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        thinking: bool = False,
        tools: Optional[List[ToolDefinition]] = None,
    ) -> ProviderResponse:
        return self.drain_stream(
            self.stream(
                messages=messages,
                system_prompt=system_prompt,
                thinking=thinking,
                tools=tools,
            )
        )

    # ----------------------------------------------------------------- files

    def upload_file(self, file_path: str, mime_type: str) -> Optional[FileReference]:
        """Attachments are sent inline as base64 blocks; return a local ref."""
        return FileReference(uri=file_path, mime_type=mime_type, display_name=file_path)


__all__ = ["AnthropicProvider", "DEFAULT_MODEL", "has_ambient_credentials"]
