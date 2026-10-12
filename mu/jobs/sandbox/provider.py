"""Container-side provider adapter. It has no direct provider API capability."""
from __future__ import annotations

import json
import os

import httpx

from providers.base import LLMProvider, StreamEvent
from .model_relay import wire


class RelayProvider(LLMProvider):
    def __init__(self, name, model):
        super().__init__(model)
        self.name = name

    def get_available_models(self):
        return [self.model_name]

    def upload_file(self, file_path, mime_type):
        raise RuntimeError("upload files through inline media in a job sandbox")

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return self.drain_stream(self.stream(messages, system_prompt, thinking, tools))

    def stream(self, messages, system_prompt=None, thinking=False, tools=None, cache_hint=None, reasoning_effort=None):
        payload = wire(dict(messages=messages, system_prompt=system_prompt, thinking=thinking,
                            tools=tools, cache_hint=cache_hint, reasoning_effort=reasoning_effort))
        transport = httpx.HTTPTransport(uds=os.environ["MUCLI_MODEL_RELAY"])
        with httpx.Client(transport=transport, timeout=300, trust_env=False) as client:
            with client.stream("POST", "http://relay/stream", json=payload,
                               headers={"X-Mucli-Stage-Token": os.environ["MUCLI_STAGE_TOKEN"]}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line:
                        event = StreamEvent(**json.loads(line))
                        if event.kind == "error":
                            raise RuntimeError(event.text)
                        yield event
