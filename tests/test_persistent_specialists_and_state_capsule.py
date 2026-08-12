"""Regression coverage for Muse-inspired persistent-state harness changes."""
from __future__ import annotations

from types import SimpleNamespace

from mu.agent.registry import SubagentRecord, SubagentRegistry
from mu.session.state_capsule import build_state_capsule
from mu.tools.agent.spawn import _build_system_prompt, _infer_specialist_key, _short_task_title


class _Lifecycle:
    def snapshot(self):
        return {}


class _Memory:
    def __init__(self, entries=None):
        self.entries = list(entries or [])

    def save(self, **kwargs):
        self.entries.append(SimpleNamespace(
            content=kwargs["content"], tags=kwargs.get("tags", []), source=kwargs.get("source", ""),
            kind=kwargs.get("kind", "finding"), status=kwargs.get("status", "active"), updated_at=1.0,
        ))


class _SM:
    def __init__(self):
        self.history = []
        self.task_memory = _Memory()
        self.turn_scratchpad = _Memory()
        self.conversation_summary = ""
        self._legacy_calls = []

    def get_feature_state(self):
        return {"feature_name": "capsule", "status": "in_progress", "next_phase": "verify"}

    def force_progress_checkpoint(self, provider=None, *, min_new_entries=6):
        self._legacy_calls.append(provider)
        return True


def test_subagent_titles_are_concise_complete_action_labels():
    assert _short_task_title(
        "Security audit of the rsq-mgmt-cluster repository. Review tenant isolation."
    ) == "Security audit"
    assert _short_task_title(
        "Code quality review of rsq-mgmt-cluster-management-ui. Focus on server.js."
    ) == "Code quality review"
    assert _short_task_title(
        "Infrastructure & K8s audit of rsq-mgmt-cluster. Review manifests."
    ) == "Infrastructure & K8s audit"
    assert _short_task_title("A long task body", "API contract review") == "API contract review"
    assert _short_task_title(
        "Review the API",
        "Review every endpoint in the entire public API surface",
        "code_review",
    ) == "Review the API"


def test_persistent_specialist_pool_reuses_exact_profile_only():
    reg = SubagentRegistry()
    child = SimpleNamespace()
    worker = reg.register_specialist(
        child, specialist_key="repo", depth=1, model="m", provider_key="p", disabled_tools=["write_file"]
    )
    # Completion releases the worker rather than deleting its Session.
    rec = SubagentRecord(task_id="sa-x", task="x", depth=1, child=child, lifecycle=_Lifecycle(), worker_id=worker.worker_id)
    reg._release_specialist(rec)

    reused = reg.acquire_specialist("repo", depth=1, model="m", provider_key="p", disabled_tools=["write_file"])
    assert reused is worker
    assert reused.child is child
    assert reused.delegation_count == 2

    reg._release_specialist(rec)
    assert reg.acquire_specialist("tests", depth=1, model="m", provider_key="p", disabled_tools=["write_file"]) is None
    assert reg.acquire_specialist("repo", depth=1, model="m", provider_key="other", disabled_tools=["write_file"]) is None


def test_mailbox_is_sparse_and_one_shot():
    reg = SubagentRegistry()
    child = SimpleNamespace()
    worker = reg.register_specialist(child, specialist_key="repo", depth=1, model="m", provider_key="p")
    rec = reg.register(child, task="inspect auth", depth=1, lifecycle=_Lifecycle(), specialist_key="repo", worker_id=worker.worker_id)

    assert reg.publish_finding(rec.task_id, "Auth middleware is bypassed", importance="high")["ok"] is True
    first = reg.context_block()
    second = reg.context_block()
    assert "Auth middleware is bypassed" in first
    assert "running" not in first.lower()  # live progress is UI-only
    assert second == ""


def test_deterministic_state_capsule_projects_structured_runtime_state_without_llm():
    sm = _SM()
    sm.history = [
        {"role": "tool", "parts": [{
            "type": "tool_result", "tool_name": "apply_diff", "cache_key": "edit-1",
            "tool_result": {"tool_name": "apply_diff", "ok": True, "summary": "patched auth", "modified_files": ["src/auth.py"]},
        }]},
        {"role": "tool", "parts": [{
            "type": "tool_result", "tool_name": "bash", "cache_key": "test-1",
            "tool_result": {"tool_name": "bash", "ok": False, "error_code": "exit_1", "summary": "pytest test_refresh failed", "args": "pytest test_refresh"},
        }]},
    ]
    sm.task_memory.entries.append(SimpleNamespace(
        content="Keep JWT format backwards compatible", kind="decision", status="active", updated_at=5.0, tags=[]
    ))
    session = SimpleNamespace(session_manager=sm, variables={"session_goal": "Fix refresh auth"})

    capsule = build_state_capsule(session)
    assert "Fix refresh auth" in capsule
    assert "src/auth.py" in capsule
    assert "pytest test_refresh failed" in capsule
    assert "Keep JWT format backwards compatible" in capsule
    assert "feature: capsule" in capsule

    # The projector replaces periodic progress checkpoint summarization with a
    # deterministic no-model checkpoint. The original function is never called.
    assert sm.force_progress_checkpoint(provider=object()) is False
    assert sm._legacy_calls == []


def test_specialist_persona_is_stable_and_does_not_embed_mutable_task():
    prompt = _build_system_prompt(
        task="SECRET MUTABLE TASK", remaining_depth=1, max_iterations=99,
        parent_findings="TRANSIENT PARENT FACT", specialist="repo", depth=1,
    )
    assert "persistent SUB-AGENT" in prompt
    assert "repo" in prompt
    assert "SECRET MUTABLE TASK" not in prompt
    assert "TRANSIENT PARENT FACT" not in prompt


def test_specialist_routing_is_deterministic():
    assert _infer_specialist_key("investigate where auth is initialized") == "repo"
    assert _infer_specialist_key("fix the authentication bug") == "implementation"
    assert _infer_specialist_key("run failing pytest tests") == "tests"
    assert _infer_specialist_key("anything", "My Specialist") == "my-specialist"
