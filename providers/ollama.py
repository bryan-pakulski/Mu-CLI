"""Ollama provider — first-class local LLM support.

Features:
  * **Standard env vars**: `OLLAMA_HOST` overrides the connection target
    (matches the official `ollama` CLI's convention). Falls back to
    `OLLAMA_API_KEY` + `https://ollama.com` if you want the hosted
    service, then `http://localhost:11434` if neither is set.
  * **Preflight check** with actionable error messages — distinguishes
    "Ollama not running" from "model not pulled" from generic transport
    errors, each with the exact CLI command to fix.
  * **Vision** — `MessagePart(type="image_input", image=ImageData)`
    rides on Ollama's `images: [base64]` message field, supported by
    llava, llava-llama3, qwen2-vl, llama3.2-vision, etc.
  * **Reasoning models** — parses `<think>…</think>` blocks out of the
    streamed content and emits them as `thinking_delta` events so the
    harness's existing thinking-tracking telemetry works against
    deepseek-r1, qwen-think, gpt-oss reasoning variants, etc.
  * **Native options** — passes through Ollama-specific tuning knobs
    (`num_ctx`, `num_predict`, `temperature`, `top_p`, `top_k`,
    `repeat_penalty`, `seed`, `mirostat`) from session variables
    prefixed `ollama_*`, plus an `OllamaOptions` constructor arg.
  * **Tool calling**, **keep_alive**, **NDJSON streaming**, and
    **structured tool_call deltas** as before.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from .base import (
    CacheHint,
    FileReference,
    LLMProvider,
    Message,
    MessagePart,
    ProviderResponse,
    StreamEvent,
    ToolDefinition,
)


# Native Ollama options that we expose to callers. Map: kwarg name → JSON
# field name. The Ollama API's `options` object accepts these directly.
_OLLAMA_OPTION_KEYS = (
    "num_ctx",
    "num_predict",
    "temperature",
    "top_p",
    "top_k",
    "repeat_penalty",
    "seed",
    "mirostat",
    "mirostat_eta",
    "mirostat_tau",
    "tfs_z",
    "stop",
)


@dataclass
class OllamaOptions:
    """Provider-specific options. None values are omitted from the payload
    so Ollama applies its own defaults."""

    num_ctx: Optional[int] = None
    num_predict: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    repeat_penalty: Optional[float] = None
    seed: Optional[int] = None
    mirostat: Optional[int] = None
    mirostat_eta: Optional[float] = None
    mirostat_tau: Optional[float] = None
    tfs_z: Optional[float] = None
    stop: Optional[List[str]] = None

    def as_payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key in _OLLAMA_OPTION_KEYS:
            val = getattr(self, key, None)
            if val is not None:
                out[key] = val
        return out


class OllamaError(RuntimeError):
    """Raised when the Ollama daemon is unreachable, a model isn't pulled,
    or the API returns an unrecognized error. Carries an `actionable`
    message suitable for direct display to the user.
    """

    def __init__(self, message: str, *, actionable: Optional[str] = None):
        super().__init__(message)
        self.actionable = actionable or message


def _resolve_host(explicit: Optional[str] = None) -> str:
    """Pick the right Ollama endpoint.

    Priority:
      1. Explicit constructor `host=` argument
      2. `OLLAMA_HOST` environment variable (official CLI convention)
      3. `https://ollama.com` if `OLLAMA_API_KEY` is set (hosted service)
      4. `http://localhost:11434` (default local daemon)
    """
    if explicit:
        host = explicit
    else:
        env_host = os.environ.get("OLLAMA_HOST")
        if env_host:
            host = env_host
        elif os.environ.get("OLLAMA_API_KEY"):
            host = "https://ollama.com"
        else:
            host = "http://localhost:11434"
    # OLLAMA_HOST often comes as `host:port` without a scheme; normalize.
    if "://" not in host:
        host = f"http://{host}"
    return host.rstrip("/")


def _split_think_blocks(text: str, *, in_think: bool) -> tuple:
    """Split a streamed content chunk on `<think>…</think>` boundaries.

    Returns `(content_chunks, think_chunks, new_in_think)`. The boundary
    tracking is stateful — callers must thread `in_think` from the
    previous chunk so a `<think>` opening in chunk N and the matching
    `</think>` in chunk N+5 are handled correctly.

    Example for `before<think>plan</think>after`:
      content_chunks = ["before", "after"]
      think_chunks   = ["plan"]
      new_in_think   = False
    """
    content_chunks: List[str] = []
    think_chunks: List[str] = []
    remaining = text
    while remaining:
        if in_think:
            close_idx = remaining.find("</think>")
            if close_idx < 0:
                think_chunks.append(remaining)
                remaining = ""
            else:
                think_chunks.append(remaining[:close_idx])
                remaining = remaining[close_idx + len("</think>") :]
                in_think = False
        else:
            open_idx = remaining.find("<think>")
            if open_idx < 0:
                content_chunks.append(remaining)
                remaining = ""
            else:
                if open_idx > 0:
                    content_chunks.append(remaining[:open_idx])
                remaining = remaining[open_idx + len("<think>") :]
                in_think = True
    return content_chunks, think_chunks, in_think


def _classify_url_error(host: str, exc: BaseException) -> OllamaError:
    """Turn an arbitrary URLError / OSError into a user-actionable message."""
    msg = str(exc)
    lowered = msg.lower()
    if "connection refused" in lowered or "name or service not known" in lowered or "no route to host" in lowered:
        return OllamaError(
            f"Ollama at {host} is not reachable.",
            actionable=(
                f"Ollama daemon not reachable at {host}.\n"
                f"Fix:\n"
                f"  - If running locally: `ollama serve` in another shell.\n"
                f"  - If remote: set `OLLAMA_HOST=<host:port>` and confirm the daemon is listening.\n"
                f"  - To check installed models: `ollama list`.\n"
                f"Underlying error: {msg}"
            ),
        )
    return OllamaError(f"Ollama transport error: {msg}", actionable=msg)


def _classify_api_error_body(host: str, model: str, body: str) -> OllamaError:
    """Distinguish 'model not pulled' / 'context overflow' from other API errors."""
    lowered = body.lower()
    if "model" in lowered and ("not found" in lowered or "could not be loaded" in lowered):
        return OllamaError(
            f"Ollama model '{model}' is not installed.",
            actionable=(
                f"The model '{model}' isn't installed on the Ollama daemon at {host}.\n"
                f"Fix: `ollama pull {model}` (then retry).\n"
                f"To list installed models: `ollama list`."
            ),
        )
    if "prompt too long" in lowered or (
        "exceed" in lowered and "context" in lowered
    ):
        return OllamaError(
            f"Ollama context overflow for '{model}': {body[:200]}",
            actionable=(
                f"The prompt exceeds the model's context window. The harness "
                f"compactor should prevent this — check that "
                f"`/set ollama_num_ctx <n>` matches your model's real window, "
                f"or `unset ollama_num_ctx` to let the harness auto-detect it "
                f"from `/api/show`.\n"
                f"Quick recovery: `/clear` to drop history, or aggressively "
                f"lower `context_trim_threshold` (e.g. `/set context_trim_threshold 0.5`)."
            ),
        )
    return OllamaError(f"Ollama API error: {body[:300]}", actionable=body[:500])


class OllamaProvider(LLMProvider):
    API_KEY = os.getenv("OLLAMA_API_KEY")

    def __init__(
        self,
        model_name: str = "",
        host: Optional[str] = None,
        *,
        options: Optional[OllamaOptions] = None,
        request_timeout: float = 300.0,
    ):
        super().__init__(model_name)
        self.name = "ollama"
        self.host = _resolve_host(host)
        self.options = options or OllamaOptions()
        self.request_timeout = float(request_timeout)
        # Cache the preflight result so we don't probe the daemon on every
        # request. Reset by `invalidate_preflight()`.
        self._preflight_done = False
        self._preflight_error: Optional[OllamaError] = None
        self._cached_models: Optional[List[str]] = None
        # Model name → trained context length (tokens), or None if unknown.
        # Populated lazily by `_fetch_context_length`.
        self._context_length_cache: Dict[str, Optional[int]] = {}

    # ----------------------------------------------------------- preflight

    def invalidate_preflight(self) -> None:
        self._preflight_done = False
        self._preflight_error = None
        self._cached_models = None
        self._context_length_cache: Dict[str, Optional[int]] = {}

    def _fetch_context_length(self, model_name: str) -> Optional[int]:
        """Hit `/api/show` and return the model's trained context length, or
        None if the endpoint is unreachable / the field is missing. Cached
        per model so we only probe once per process."""
        if not model_name:
            return None
        if not hasattr(self, "_context_length_cache"):
            self._context_length_cache = {}
        if model_name in self._context_length_cache:
            return self._context_length_cache[model_name]
        try:
            payload = json.dumps({"model": model_name}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.host}/api/show",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    **self._auth_headers(),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.request_timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
            self._context_length_cache[model_name] = None
            return None
        # Ollama's /api/show returns a `model_info` dict whose keys are
        # namespaced by the model architecture, e.g.
        # `llama.context_length`, `qwen2.context_length`. Grab whichever
        # one is present.
        ctx_len: Optional[int] = None
        model_info = data.get("model_info") or {}
        if isinstance(model_info, dict):
            for k, v in model_info.items():
                if k.endswith(".context_length") and isinstance(v, int) and v > 0:
                    ctx_len = v
                    break
        self._context_length_cache[model_name] = ctx_len
        return ctx_len

    def effective_response_reserve(
        self, model_name: Optional[str] = None
    ) -> Optional[int]:
        """Compactor reserve for Ollama, derived from `ollama_num_predict`.

        Resolution:
          * num_predict > 0  → that's the explicit output cap; reserve it
                                exactly so the input budget gets the rest.
          * num_predict <= 0 → Ollama's "unlimited" / model-default mode.
                                Heuristically reserve ~⅛ of the window
                                (clamped to [512, 2048]) so a long
                                multi-tool-call output still has room.
          * No num_predict knob and no window → None (fall back to var).
        """
        try:
            vars_ = getattr(self, "_session_variables", None) or {}
            raw = vars_.get("ollama_num_predict")
            if raw is not None:
                num_predict = int(raw)
                if num_predict > 0:
                    return num_predict
        except (TypeError, ValueError):
            pass
        window = self.effective_context_window(model_name)
        if not window:
            return None
        return max(512, min(2048, window // 8))

    def effective_context_window(
        self, model_name: Optional[str] = None
    ) -> Optional[int]:
        """Real input-context ceiling for the active Ollama model.

        Resolution order:
          1. `ollama_num_ctx` session variable, if > 0 — the user is
             explicitly overriding (and is responsible for sanity).
          2. The model's trained `context_length` from `/api/show`.
          3. None (caller falls back to the harness-wide default).

        The session compactor calls this on every turn so we never send
        a prompt that's larger than the model can read — preventing the
        "prompt too long; exceeded max context length" 400 from Ollama.
        """
        # Resolve from session variables first.
        try:
            vars_ = getattr(self, "_session_variables", None) or {}
            raw = vars_.get("ollama_num_ctx")
            if raw is not None:
                num_ctx = int(raw)
                if num_ctx > 0:
                    return num_ctx
        except (TypeError, ValueError):
            pass
        target_model = model_name or self.model_name
        return self._fetch_context_length(target_model)

    def preflight(self) -> None:
        """Probe the daemon and cache the result.

        Raises `OllamaError` with an actionable message on failure. Safe
        to call multiple times — only the first call hits the network.
        Tests can force a re-check via `invalidate_preflight()`.
        """
        if self._preflight_done:
            if self._preflight_error is not None:
                raise self._preflight_error
            return
        try:
            models = self._fetch_models()
        except OllamaError as exc:
            self._preflight_done = True
            self._preflight_error = exc
            raise
        self._cached_models = models
        self._preflight_done = True

    # -------------------------------------------------------- API helpers

    def _auth_headers(self) -> Dict[str, str]:
        if self.API_KEY:
            return {"Authorization": f"Bearer {self.API_KEY}"}
        return {}

    def _fetch_models(self) -> List[str]:
        try:
            req = urllib.request.Request(
                f"{self.host}/api/tags", headers=self._auth_headers()
            )
            with urllib.request.urlopen(req, timeout=self.request_timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except urllib.error.URLError as exc:
            raise _classify_url_error(self.host, exc)
        except (json.JSONDecodeError, OSError) as exc:
            raise OllamaError(
                f"Could not parse Ollama tags response from {self.host}: {exc}",
                actionable=str(exc),
            )

    def get_available_models(self) -> List[str]:
        """Return installed models. Never raises — returns [] on failure.

        For an actionable error use `preflight()` instead.
        """
        if self._cached_models is not None:
            return list(self._cached_models)
        try:
            return self._fetch_models()
        except OllamaError:
            return []

    def is_model_installed(self, model: str) -> bool:
        if not model:
            return False
        installed = self.get_available_models()
        if model in installed:
            return True
        # Ollama tags include a `:latest` suffix that's often elided.
        normalized = model.split(":", 1)[0]
        return any(m.split(":", 1)[0] == normalized for m in installed)

    # ------------------------------------------------------- message conversion

    def _convert_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        """Convert internal Message format → Ollama /api/chat shape.

        * Text parts join into `content`.
        * `image_input` parts (raw bytes) get base64-encoded into the
          message's `images` field (vision models read this).
        * `tool_call` parts become `tool_calls` on the assistant message.
        * `tool_result` parts become a `tool` role message.
        """
        ollama_msgs: List[Dict[str, Any]] = []
        for msg in messages:
            content = ""
            tool_calls: List[Dict[str, Any]] = []
            images: List[str] = []
            role = msg.role

            for part in msg.parts:
                if part.type == "text":
                    content += (part.text or "") + "\n"
                elif part.type == "image_input" and part.image is not None:
                    try:
                        encoded = base64.b64encode(part.image.data).decode("ascii")
                    except Exception:
                        encoded = ""
                    if encoded:
                        images.append(encoded)
                elif part.type == "tool_call":
                    tool_calls.append(
                        {
                            "function": {
                                "name": part.tool_name,
                                "arguments": part.tool_args,
                            }
                        }
                    )
                    role = "assistant"
                elif part.type == "tool_result":
                    role = "tool"
                    if isinstance(part.tool_result, (dict, list)):
                        content = json.dumps(part.tool_result, indent=2, sort_keys=True)
                    else:
                        content = str(part.tool_result)

            message_dict: Dict[str, Any] = {
                "role": role,
                "content": content.strip(),
            }
            if tool_calls:
                message_dict["tool_calls"] = tool_calls
            if images:
                message_dict["images"] = images
            ollama_msgs.append(message_dict)
        return ollama_msgs

    # -------------------------------------------------- option resolution

    def _build_options(
        self,
        *,
        thinking: bool,
        reasoning_effort: Optional[str],
        session_variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build the `options` dict for an /api/chat call.

        Precedence (later wins):
          1. Constructor `options=` arg
          2. Session variables prefixed `ollama_<option>`
          3. `thinking=True` → mild temperature bump if not otherwise set
        """
        opts = self.options.as_payload()
        if session_variables:
            for key in _OLLAMA_OPTION_KEYS:
                var_key = f"ollama_{key}"
                if var_key in session_variables:
                    val = session_variables[var_key]
                    # Treat None / "" / 0 as "no override" so the config-
                    # registry sentinel defaults (0) don't silently force
                    # Ollama into degenerate values. Users who actually
                    # want temperature=0 can still set 0.0 via the
                    # constructor's `options=OllamaOptions(temperature=0)`.
                    if val is None or val == "" or val == 0 or val == 0.0:
                        continue
                    opts[key] = val
        if thinking and "temperature" not in opts:
            opts["temperature"] = 0.7
        if reasoning_effort:
            # Ollama doesn't have a native reasoning_effort knob; surface
            # it as `num_predict` cap when "low" / "medium".
            opts.setdefault("reasoning_effort", reasoning_effort)
        return opts

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
        # Surface preflight errors as actionable messages instead of opaque
        # transport errors mid-stream. We only check once per provider.
        try:
            self.preflight()
        except OllamaError as exc:
            yield StreamEvent(kind="error", text=exc.actionable)
            raise

        if self.model_name and not self.is_model_installed(self.model_name):
            err = OllamaError(
                f"Ollama model '{self.model_name}' is not installed.",
                actionable=(
                    f"The model '{self.model_name}' isn't installed at {self.host}.\n"
                    f"Fix: `ollama pull {self.model_name}` then retry.\n"
                    f"Installed: {', '.join(self.get_available_models()) or '(none)'}"
                ),
            )
            yield StreamEvent(kind="error", text=err.actionable)
            raise err

        # Pull session variables off the provider if a caller set them.
        session_variables = getattr(self, "_session_variables", None)
        ollama_messages = self._convert_messages(messages)
        if system_prompt:
            ollama_messages.insert(0, {"role": "system", "content": system_prompt})

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": ollama_messages,
            "stream": True,
            "options": self._build_options(
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                session_variables=session_variables,
            ),
        }
        # keep_alive keeps the model warm across turns.
        keep_alive = cache_hint.keep_alive_seconds if cache_hint else 600
        payload["keep_alive"] = keep_alive
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        headers = {"Content-Type": "application/json", **self._auth_headers()}
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )

        emitted_tool_index = 0
        last_in = 0
        last_out = 0
        in_think = False  # state for cross-chunk <think>…</think> tracking

        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as response:
                for raw in response:
                    if not raw:
                        continue
                    try:
                        chunk = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue

                    # Ollama returns errors mid-stream as { "error": "..." }.
                    if "error" in chunk and not chunk.get("message"):
                        err = _classify_api_error_body(
                            self.host, self.model_name, str(chunk.get("error", ""))
                        )
                        yield StreamEvent(kind="error", text=err.actionable)
                        raise err

                    msg = chunk.get("message") or {}
                    content = msg.get("content") or ""
                    if content:
                        content_parts, think_parts, in_think = _split_think_blocks(
                            content, in_think=in_think
                        )
                        for piece in content_parts:
                            if piece:
                                yield StreamEvent(kind="text_delta", text=piece)
                        for piece in think_parts:
                            if piece:
                                yield StreamEvent(kind="thinking_delta", text=piece)

                    # Models may also use a structured `thinking` field —
                    # surface it identically.
                    thought = msg.get("thinking") or msg.get("reasoning")
                    if thought:
                        yield StreamEvent(kind="thinking_delta", text=str(thought))

                    for tc in msg.get("tool_calls", []) or []:
                        fn = tc.get("function") or {}
                        cid = f"ollama_call_{emitted_tool_index}"
                        emitted_tool_index += 1
                        yield StreamEvent(
                            kind="tool_call_start",
                            tool_name=fn.get("name"),
                            tool_call_id=cid,
                        )
                        yield StreamEvent(
                            kind="tool_call_complete",
                            tool_name=fn.get("name"),
                            tool_args=fn.get("arguments") or {},
                            tool_call_id=cid,
                        )

                    last_in = chunk.get("prompt_eval_count", last_in) or last_in
                    last_out = chunk.get("eval_count", last_out) or last_out
                    if chunk.get("done"):
                        break
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                pass
            err = _classify_api_error_body(self.host, self.model_name, body or str(exc))
            yield StreamEvent(kind="error", text=err.actionable)
            raise err
        except urllib.error.URLError as exc:
            err = _classify_url_error(self.host, exc)
            yield StreamEvent(kind="error", text=err.actionable)
            raise err

        yield StreamEvent(
            kind="usage",
            input_tokens=last_in,
            output_tokens=last_out,
            total_tokens=last_in + last_out,
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
        return FileReference(uri=file_path, mime_type=mime_type, display_name=file_path)

    # ------------------------------------------------------ session helper

    def bind_session_variables(self, variables: Dict[str, Any]) -> None:
        """Wire a session's variables dict so the provider can read
        `ollama_*` overrides on each call. Called by the harness when
        constructing the provider; safe to omit in standalone use.
        """
        self._session_variables = variables


__all__ = [
    "OllamaError",
    "OllamaOptions",
    "OllamaProvider",
]
