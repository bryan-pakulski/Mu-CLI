"""Regression coverage for durable asynchronous sub-agent results."""
from __future__ import annotations

import json

from mu.agent.subagent_artifacts import SubagentArtifactStore


class _ArtifactRegistry:
    def __init__(self):
        self.items = []

    def add(self, **kwargs):
        item = {
            "artifact_id": f"a-{len(self.items) + 1}",
            "name": kwargs["name"],
            "kind": kwargs.get("kind", "file"),
            "mime_type": kwargs.get("mime_type", "application/octet-stream"),
        }
        self.items.append((item, kwargs["content"]))
        return item


def test_subagent_store_persists_progress_and_final_bundle(tmp_path):
    artifacts = _ArtifactRegistry()
    store = SubagentArtifactStore(str(tmp_path), artifacts)
    started = store.start("sa-test", {
        "task": "inspect the repository",
        "depth": 1,
        "batch_id": "batch-1",
        "model": "test-model",
    })
    assert started["status"] == "running"

    store.record_event(
        "sa-test",
        {"kind": "subagent_progress", "iter": 2, "last_tool": "read_file"},
        state_patch={"iter": 2, "last_tool": "read_file", "tool_count": 3},
    )
    finished = store.finish(
        "sa-test",
        {
            "status": "done",
            "summary": "Found and fixed the issue.",
            "tool_calls": 3,
            "history_length": 2,
        },
        [
            {"role": "assistant", "parts": [{"type": "text", "text": "working"}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "done"}]},
        ],
    )
    assert finished["status"] == "done"
    assert finished["artifact"]["artifact_id"] == "a-1"
    assert store.load("sa-test")["summary"] == "Found and fixed the issue."

    root = tmp_path / "subagents" / "sa-test"
    assert (root / "events.jsonl").exists()
    bundle = json.loads((root / "result.json").read_text())
    assert bundle["state"]["tool_calls"] == 3
    assert len(bundle["history"]) == 2
    assert artifacts.items[0][0]["name"] == "subagent-sa-test.json"
