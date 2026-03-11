import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from mu_cli.webapp.services_runtime import (
    RuntimeMutationDeps,
    mutate_runtime_for_clear,
    mutate_runtime_for_new_session,
    mutate_runtime_for_settings,
)


class _SessionStore:
    def __init__(self) -> None:
        self.used: list[str] = []

    def use(self, name: str) -> None:
        self.used.append(name)


class _WorkspaceStore:
    def __init__(self) -> None:
        self.snapshot = None

    def attach(self, path: Path):
        snap = SimpleNamespace(root=str(path), files=["a.py", "b.py"])
        self.snapshot = snap
        return snap

    def summary(self) -> str:
        return "workspace summary"


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        provider="echo",
        model="echo",
        openai_api_key=None,
        google_api_key=None,
        ollama_endpoint=None,
        approval_mode="auto",
        debug=False,
        debug_level="info",
        agentic_planning=False,
        research_mode=False,
        max_runtime_seconds=60,
        condense_enabled=True,
        condense_window=12,
        ollama_context_window=65536,
        enabled_skills=[],
        workspace_path=None,
        workspace_store=_WorkspaceStore(),
        session_name="default",
        session_store=_SessionStore(),
        base_tools=[SimpleNamespace(name="read_file"), SimpleNamespace(name="write_file")],
        enabled_tools={"read_file": True, "write_file": True},
        custom_tool_specs=[],
        traces=[],
        tools=[],
        agent=SimpleNamespace(state=SimpleNamespace(messages=[SimpleNamespace(role="user", content="hi")])),
    )


def _deps(call_log: dict[str, int]) -> RuntimeMutationDeps:
    def _inc(key: str) -> None:
        call_log[key] = call_log.get(key, 0) + 1

    return RuntimeMutationDeps(
        get_models=lambda provider, api_key: ["echo", "echo-pro"],
        provider_api_key=lambda runtime: runtime.openai_api_key,
        attach_workspace_if_available=lambda runtime: _inc("attach_workspace"),
        initialize_fresh_session_state=lambda runtime: _inc("init_fresh"),
        initialize_fresh_session_state_reset_summary=lambda runtime: _inc("init_fresh_reset"),
        refresh_tooling=lambda runtime: _inc("refresh_tooling"),
        new_agent=lambda runtime: SimpleNamespace(state=SimpleNamespace(messages=[])),
        inject_planning=lambda agent, summary, git: _inc("inject_planning"),
        inject_research_prompt=lambda agent: _inc("inject_research"),
        sync_skill_prompts=lambda runtime: _inc("sync_skills"),
        git_agent_instruction=lambda runtime: "git guidance",
    )


class RuntimeMutationServiceTests(unittest.TestCase):
    def test_mutate_runtime_for_new_session_updates_state_and_initializes(self) -> None:
        runtime = _runtime()
        calls: dict[str, int] = {}
        deps = _deps(calls)

        mutate_runtime_for_new_session(
            runtime,
            {
                "provider": "echo",
                "model": "echo-pro",
                "approval_mode": "deny",
                "agentic_planning": True,
                "research_mode": True,
                "enabled_skills": ["s1", ""],
            },
            "s-new",
            deps,
        )

        self.assertEqual("s-new", runtime.session_name)
        self.assertEqual(["s-new"], runtime.session_store.used)
        self.assertEqual("echo-pro", runtime.model)
        self.assertEqual(["s1"], runtime.enabled_skills)
        self.assertEqual(1, calls.get("attach_workspace"))
        self.assertEqual(1, calls.get("init_fresh"))

    def test_mutate_runtime_for_clear_respects_reset_flag(self) -> None:
        runtime = _runtime()
        calls: dict[str, int] = {}
        deps = _deps(calls)

        mutate_runtime_for_clear(runtime, reset_summary_index=False, deps=deps)
        mutate_runtime_for_clear(runtime, reset_summary_index=True, deps=deps)

        self.assertEqual(2, calls.get("attach_workspace"))
        self.assertEqual(1, calls.get("init_fresh"))
        self.assertEqual(1, calls.get("init_fresh_reset"))

    def test_mutate_runtime_for_settings_rebuilds_agent_and_preserves_messages(self) -> None:
        runtime = _runtime()
        calls: dict[str, int] = {}
        deps = _deps(calls)

        with tempfile.TemporaryDirectory() as td:
            mutate_runtime_for_settings(
                runtime,
                {
                    "model": "echo-pro",
                    "tool_visibility": {"read_file": False},
                    "custom_tools": [{"name": "x"}],
                    "enabled_skills": ["s1", "s2"],
                    "ollama_endpoint": "http://127.0.0.1:11434",
                    "ollama_context_window": 32768,
                    "workspace": td,
                    "debug_level": "debug",
                    "agentic_planning": True,
                    "research_mode": True,
                },
                deps,
            )

        self.assertEqual("echo-pro", runtime.model)
        self.assertFalse(runtime.enabled_tools["read_file"])
        self.assertEqual([{"name": "x"}], runtime.custom_tool_specs)
        self.assertEqual(["s1", "s2"], runtime.enabled_skills)
        self.assertEqual("http://127.0.0.1:11434", runtime.ollama_endpoint)
        self.assertEqual("debug", runtime.debug_level)
        self.assertEqual(32768, runtime.ollama_context_window)
        self.assertEqual(1, calls.get("refresh_tooling"))
        self.assertEqual(1, calls.get("inject_planning"))
        self.assertEqual(1, calls.get("inject_research"))
        self.assertEqual(1, calls.get("sync_skills"))
        self.assertEqual(1, len(runtime.agent.state.messages))
        self.assertTrue(any(line.startswith("workspace-attached:") for line in runtime.traces))


if __name__ == "__main__":
    unittest.main()
