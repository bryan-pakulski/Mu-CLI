"""`/model` completion is driven by live provider discovery, not a static
model list. Covers the terminal completer, the GUI completion endpoint, the
Session -> UI registration hook, and the removal of KNOWN_MODELS."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from mu.ui.input import DynamicModelCompleter, InputHandler


class _Provider:
    def __init__(self, name, models, current=""):
        self.name = name
        self.model_name = current
        self._models = models
        self.calls = 0

    def get_available_models(self):
        self.calls += 1
        if isinstance(self._models, Exception):
            raise self._models
        return list(self._models)


def _complete(completer, text):
    return [
        c.text
        for c in completer.get_completions(Document(text), CompleteEvent())
    ]


def test_known_models_constant_is_gone():
    import utils.config as config

    assert not hasattr(config, "KNOWN_MODELS")


def test_completer_uses_active_provider_and_caches():
    handler = InputHandler()
    provider = _Provider("anthropic", ["claude-opus-5", "claude-sonnet-5"], current="claude-opus-5")
    handler.set_provider_source(lambda: provider)
    completer = DynamicModelCompleter(handler)

    assert _complete(completer, "claude-son") == ["claude-sonnet-5"]
    assert _complete(completer, "claude") == ["claude-opus-5", "claude-sonnet-5"]
    assert provider.calls == 1  # second Tab served from cache


def test_completer_follows_provider_switch_and_includes_current_model():
    handler = InputHandler()
    holder = SimpleNamespace(provider=_Provider("ollama", ["llama3"], current="llama3"))
    handler.set_provider_source(lambda: holder.provider)
    completer = DynamicModelCompleter(handler)
    assert _complete(completer, "ll") == ["llama3"]

    # /provider switched; discovery is empty but the active model still shows.
    holder.provider = _Provider("openai", [], current="gpt-5.6-terra")
    assert _complete(completer, "gpt") == ["gpt-5.6-terra"]


def test_completer_is_quiet_without_provider_or_on_discovery_failure():
    handler = InputHandler()
    completer = DynamicModelCompleter(handler)
    assert _complete(completer, "x") == []

    handler.set_provider_source(lambda: _Provider("gemini", RuntimeError("offline")))
    assert _complete(completer, "gem") == []


def test_session_registers_provider_source_on_ui():
    from mu.session.session import Session, SessionManager

    class _UI:
        def __init__(self):
            self.source = None

        def set_provider_source(self, source):
            self.source = source

    ui = _UI()
    provider = _Provider("openai", ["gpt-5.6-terra"])
    session = Session(provider, False, "sys", SessionManager(), ui=ui)
    assert ui.source is not None
    assert ui.source() is provider
    session.provider = _Provider("anthropic", ["claude-opus-5"])
    assert ui.source().name == "anthropic"


def test_gui_completion_endpoint_discovers_from_active_session():
    from mu.gui.routers.chat import completions_endpoint

    provider = _Provider("anthropic", ["claude-sonnet-5"], current="claude-opus-5")
    session = SimpleNamespace(provider=provider)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_by_name=lambda name=None: session))
    )
    result = asyncio.run(completions_endpoint(request, kind="models"))
    assert result == {"items": ["claude-opus-5", "claude-sonnet-5"]}

    empty = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_by_name=lambda name=None: None))
    )
    assert asyncio.run(completions_endpoint(empty, kind="models")) == {"items": []}
