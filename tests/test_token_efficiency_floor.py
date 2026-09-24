"""Per-request fixed-floor guards (token efficiency).

The biggest recurring cost in every provider request is the part that never
changes: the core tool schema and the static system base. These tests pin
the levers that keep that floor small without losing recoverable context:

  * rarely-needed tools live in optional lazy phases (recoverable via
    `load_tools`, auto-activated when session state makes them relevant);
  * the core schema stays under a token ceiling;
  * the wall-clock prelude renders inside LAYER 5, not at the prompt head,
    so the static prefix is byte-stable for provider prefix caching;
  * the pinned goal renders once;
  * a successful bash command that merely *prints* "no such file" is not
    classified as a tool failure (which used to poison the L2 capsule
    with stale stdout blobs).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import mu.tools.descriptors as descriptors
from mu.tools._envelope import infer_tool_error_code
from mu.tools.descriptors import (
    OPTIONAL_TOOL_PHASES,
    infer_contextual_tool_phases,
    list_tool_descriptors,
    resolve_active_tool_phases,
)

CORE_SCHEMA_TOKEN_CEILING = 9_000  # measured 7.5k after slimming; was 13.6k
PER_TOOL_TOKEN_CEILING = 600  # save_memory (rich enum params) is the largest


def _schema_tokens(descriptor) -> int:
    d = descriptor.definition
    payload = {"name": d.name, "description": d.description, "parameters": d.parameters}
    return len(json.dumps(payload)) // 4


def _core():
    return [t for t in list_tool_descriptors() if t.phase == "core"]


def test_core_schema_stays_under_token_ceiling():
    total = sum(_schema_tokens(t) for t in _core())
    assert total <= CORE_SCHEMA_TOKEN_CEILING, (
        f"core tool schema ≈{total} tok > {CORE_SCHEMA_TOKEN_CEILING}; "
        "trim descriptions or move tools into an optional phase"
    )


def test_no_single_core_tool_is_a_tutorial():
    fat = {t.definition.name: _schema_tokens(t) for t in _core()}
    fat = {k: v for k, v in fat.items() if v > PER_TOOL_TOKEN_CEILING}
    assert not fat, f"core tools over {PER_TOOL_TOKEN_CEILING} tok: {fat}"


def test_rarely_used_tools_live_in_optional_phases():
    by_name = {t.definition.name: t.phase for t in list_tool_descriptors()}
    expected = {
        "list_traces": "trace",
        "trace_summary": "trace",
        "trace_series": "trace",
        "trace_iteration": "trace",
        "manage_durable_memory": "memory_curation",
        "supersede_memory": "memory_curation",
        "retire_thread": "memory_curation",
        "archive_memory": "memory_curation",
        "list_threads": "thread",
        "send_thread_message": "thread",
        "handoff_thread_paths": "thread",
        "list_attachments": "attachment",
        "read_attachment": "attachment",
        "browser_snapshot": "browser",
        "verify_html": "browser",
        "best_of_codex": "codex",
    }
    for name, phase in expected.items():
        assert by_name.get(name) == phase, (name, by_name.get(name))
    # Every optional phase is documented for the model.
    assert set(expected.values()) <= set(OPTIONAL_TOOL_PHASES)
    # And load_tools itself stays core so they are always reachable.
    assert by_name["load_tools"] == "core"


def test_optional_phases_are_not_mode_owned():
    """Optional phases must be loadable in every mode (unlike
    feature/research/security/teacher registries)."""
    mode_phases, _ = descriptors._mode_tool_phase_policy("default")
    assert not (set(OPTIONAL_TOOL_PHASES) & mode_phases)
    for phase in OPTIONAL_TOOL_PHASES:
        out = resolve_active_tool_phases({"agent_mode": "default"}, [phase])
        assert phase in out


def test_contextual_phases_auto_activate_from_session_state():
    # Nothing relevant -> nothing inferred.
    assert infer_contextual_tool_phases(None) == []
    assert infer_contextual_tool_phases(SimpleNamespace()) == []

    # Peer thread present -> "thread".
    coordinator = SimpleNamespace(
        list_threads=lambda: [{"thread_id": "me"}, {"thread_id": "peer"}]
    )
    s = SimpleNamespace(
        thread_coordinator=coordinator,
        thread_meta=SimpleNamespace(thread_id="me"),
    )
    assert infer_contextual_tool_phases(s) == ["thread"]

    # Only self registered -> no peers -> no "thread".
    coordinator.list_threads = lambda: [{"thread_id": "me"}]
    assert infer_contextual_tool_phases(s) == []

    # Attachments present -> "attachment".
    s2 = SimpleNamespace(attachment_registry=SimpleNamespace(list=lambda limit=None: [{"id": 1}]))
    assert infer_contextual_tool_phases(s2) == ["attachment"]

    # Broken coordinator must never raise.
    def _boom():
        raise RuntimeError("db locked")

    s3 = SimpleNamespace(
        thread_coordinator=SimpleNamespace(list_threads=_boom),
        thread_meta=SimpleNamespace(thread_id="me"),
    )
    assert infer_contextual_tool_phases(s3) == []


def test_successful_bash_output_mentioning_missing_files_is_not_an_error():
    out = "STDOUT:\ngrep: mu/tools/registry.py: No such file or directory\n\nExit code: 0"
    assert infer_tool_error_code("bash", out) is None
    # Non-zero exit keeps the heuristic classification.
    failed = "STDERR:\ncat: nope.txt: No such file or directory\n\nExit code: 1"
    assert infer_tool_error_code("bash", failed) == "not_found"
    # Harness-level errors (no exit line) still classify.
    assert infer_tool_error_code("bash", "Error: No workspace attached.") == "execution_failed"
    # Other tools are unaffected.
    assert infer_tool_error_code("read_file", "Error: File does not exist") == "not_found"


def test_state_capsule_skips_goal_persistence_mirrors():
    from mu.session.state_capsule import _memory_lines

    entries = [
        SimpleNamespace(kind="goal", status="active", content="Locked session goal: ship it", updated_at=2.0),
        SimpleNamespace(kind="goal", status="active", content="Locked loop goal: segment 3", updated_at=2.0),
        SimpleNamespace(kind="decision", status="active", content="Use sqlite for the ledger", updated_at=1.0),
    ]
    session = SimpleNamespace(session_manager=SimpleNamespace(task_memory=SimpleNamespace(entries=entries)))
    lines = _memory_lines(session)
    assert lines == ["- [decision] Use sqlite for the ledger"]
