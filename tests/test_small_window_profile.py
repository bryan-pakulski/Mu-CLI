"""Small context windows (≤ 32k): compact tools, trimmed schemas, no skills index
(provider_latency_ollama_openai.md §6)."""
import json

import pytest

from mu.session.session import Session, SessionManager
from mu.workspace.folder_context import FolderContext
from providers.base import LLMProvider, MessagePart, ProviderResponse
from utils.token_estimator import estimate_tokens


class _Provider(LLMProvider):
    def __init__(self, window):
        super().__init__("scripted")
        self.name, self.window, self.seen = "scripted", window, []

    def get_available_models(self):
        return ["scripted"]

    def effective_context_window(self, model_name=None):
        return self.window

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.seen.append((system_prompt or "", list(tools or [])))
        return ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")])

    def upload_file(self, *a, **k):
        return None


def _run(tmp_path, monkeypatch, window, mode="default"):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    provider = _Provider(window)
    session = Session(provider, False, "system", SessionManager())
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    session.variables["agent_mode"] = mode
    session.sync_runtime_state()
    fc = FolderContext()
    fc.add_folder(str(workspace))
    session.session_manager.folder_context = fc  # the session re-syncs from its manager
    session.folder_context = fc
    session.send_message("hello")
    assert session.folder_context.folders, "workspace must be attached for tools to be exposed"
    system, tools = provider.seen[0]
    tool_tokens = estimate_tokens(json.dumps([{"name": t.name, "description": t.description, "parameters": t.parameters}
                                              for t in tools]))
    return system, tools, tool_tokens


def test_small_windows_get_a_compact_first_request(tmp_path, monkeypatch):
    system, tools, tool_tokens = _run(tmp_path, monkeypatch, 16_000)
    assert estimate_tokens(system) + tool_tokens < 8_000  # under half of the window
    names = {t.name for t in tools}
    assert {"read_file", "bash", "apply_diff", "search_for_string"} <= names
    assert all(len(t.description) <= 160 for t in tools)


def test_large_windows_keep_the_full_tool_surface(tmp_path, monkeypatch):
    _s1, small_tools, small_tokens = _run(tmp_path / "a", monkeypatch, 16_000)
    _s2, big_tools, big_tokens = _run(tmp_path / "b", monkeypatch, 1_000_000)
    assert len(big_tools) > len(small_tools) and big_tokens > small_tokens * 1.5


@pytest.mark.parametrize("mode", ["research", "feature", "teacher", "security", "debug", "loop"])
def test_modes_keep_their_own_tools_in_the_compact_profile(tmp_path, monkeypatch, mode):
    from mu.tools.descriptors import TOOL_DESCRIPTORS, _mode_tool_phase_policy
    _s, small_tools, _t = _run(tmp_path / "s", monkeypatch, 16_000, mode)
    _b, big_tools, _t2 = _run(tmp_path / "b", monkeypatch, 1_000_000, mode)
    _phases, active = _mode_tool_phase_policy(mode)
    mode_tools = {t.name for t in big_tools if getattr(TOOL_DESCRIPTORS.get(t.name), "phase", "core") in active}
    assert mode_tools <= {t.name for t in small_tools}


def test_profile_can_be_forced_off(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from mu.session.budgets import small_window
    session = lambda value, window: SimpleNamespace(variables={"small_window_profile": value},
                                                    provider=SimpleNamespace(model_name="m", effective_context_window=lambda m: window))
    assert small_window(session("auto", 16_000)) and not small_window(session("auto", 65_536))
    assert not small_window(session("off", 16_000)) and small_window(session("on", 1_000_000))
