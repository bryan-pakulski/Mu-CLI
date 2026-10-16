"""Container-side provider adapter. It has no direct provider API capability."""
from __future__ import annotations

import json
import os

import httpx

from providers.base import REPLAY_CURRENT_TURN, REPLAY_LAST_TOOL_TURN, LLMProvider, StreamEvent, project_replay
from .model_relay import wire


import dataclasses as _dataclasses

_EVENT_FIELDS = {f.name for f in _dataclasses.fields(StreamEvent)}


class RelayProvider(LLMProvider):
    def __init__(self, name, model, context_window=None):
        super().__init__(model)
        self.name = name
        self.context_window = int(context_window) if context_window else None

    def effective_context_window(self, model_name=None):
        """The host provider's real window for the stage model (sent in request.json), so the
        session's preflight compaction works against the true limit instead of a generic cap."""
        return self.context_window

    def get_available_models(self):
        return [self.model_name]

    def upload_file(self, file_path, mime_type):
        raise RuntimeError("upload files through inline media in a job sandbox")

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return self.drain_stream(self.stream(messages, system_prompt, thinking, tools))

    @property
    def replay_scope(self):
        """The host provider's replay scope: Anthropic needs thinking for the open tool loop's
        latest turn only; the others keep the current turn's replay data."""
        return REPLAY_LAST_TOOL_TURN if str(self.name or "").lower() == "anthropic" else REPLAY_CURRENT_TURN

    def stream(self, messages, system_prompt=None, thinking=False, tools=None, cache_hint=None, reasoning_effort=None):
        # Request projection: stale replay blobs (old thinking signatures, encrypted reasoning)
        # never cross the relay; the host adapter would drop them anyway.
        messages = project_replay(list(messages or []), self.replay_scope)
        payload = wire(dict(messages=messages, system_prompt=system_prompt, thinking=thinking,
                            tools=tools, cache_hint=cache_hint, reasoning_effort=reasoning_effort))
        transport = httpx.HTTPTransport(uds=os.environ["MUCLI_MODEL_RELAY"])
        timeout = float(os.environ.get("MUCLI_MODEL_REQUEST_TIMEOUT") or 300)
        with httpx.Client(transport=transport, timeout=timeout, trust_env=False) as client:
            with client.stream("POST", "http://relay/stream", json=payload,
                               headers={"X-Mucli-Stage-Token": os.environ["MUCLI_STAGE_TOKEN"]}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line:
                        raw = json.loads(line)
                        # Forward-compatible: ignore fields a newer host relay may add.
                        event = StreamEvent(**{k: v for k, v in raw.items() if k in _EVENT_FIELDS})
                        if event.kind == "error":
                            raise RuntimeError(event.text)
                        yield event
