import json
from types import SimpleNamespace

from mu.session.context import inject_hierarchical_context
from mu.session.editor_context import (
    build_context_receipt,
    normalise_editor_context,
    render_editor_context,
    sanitise_editor_tool_history,
    sanitise_legacy_editor_history,
    strip_legacy_editor_context_text,
)


def sample_context(marker="LIVE_EDITOR_ONLY"):
    return {
        "version": 2,
        "revision": "rev-1",
        "workspace": "/workspace/project",
        "live": {
            "path": "src/main.py",
            "filetype": "python",
            "cursor": {"line": 12, "column": 4},
            "viewport": {
                "start_line": 4,
                "end_line": 20,
                "content": f"def current():\n    return '{marker}'",
            },
            "changedtick": 9,
            "modified": True,
        },
        "turn": [
            {
                "id": "turn-1",
                "type": "selection",
                "path": "src/a.py",
                "start_line": 2,
                "end_line": 3,
                "content": "turn selection",
            }
        ],
        "pinned": [
            {
                "id": "pin-1",
                "type": "selection",
                "path": "src/b.py",
                "start_line": 8,
                "end_line": 9,
                "content": "pinned selection",
                "stale": True,
            }
        ],
        "diagnostics": [
            {
                "path": "src/main.py",
                "line": 12,
                "column": 4,
                "severity": "warning",
                "message": "example warning",
            }
        ],
        "open_buffers": [{"path": "src/main.py", "changedtick": 9}],
        "budget": {
            "max_chars": 48000,
            "included_chars": 120,
            "approx_tokens": 30,
        },
    }


def test_normalise_render_and_receipt_keep_source_out_of_receipt():
    context = normalise_editor_context(sample_context())
    rendered = render_editor_context(context)
    receipt = build_context_receipt(context)

    assert "LIVE_EDITOR_ONLY" in rendered
    assert "current turn only" in rendered
    assert receipt["live"]["path"] == "src/main.py"
    assert receipt["turn_count"] == 1
    assert receipt["pinned_count"] == 1
    assert receipt["stale_count"] == 1
    assert "LIVE_EDITOR_ONLY" not in json.dumps(receipt)


def test_rendered_context_occupies_ephemeral_layer_four_only_when_present():
    session = SimpleNamespace(
        variables={
            "conversation_summary_char_limit": 0,
            "session_type": "workspace",
            "session_role": "",
        },
        session_manager=SimpleNamespace(conversation_summary=""),
        _turn_editor_context_block="CURRENT_EDITOR_MARKER",
        _build_active_goal_context=lambda: "",
        _build_context_files_block=lambda: "",
        _build_skills_block=lambda **_kwargs: "",
    )

    prompt = inject_hierarchical_context(
        session,
        "base",
        cached_skills="",
        cached_context_files="",
    )
    assert "LAYER 4" in prompt
    assert "CURRENT_EDITOR_MARKER" in prompt
    assert "never conversation memory" in prompt

    session._turn_editor_context_block = ""
    prompt_without_context = inject_hierarchical_context(
        session,
        "base",
        cached_skills="",
        cached_context_files="",
    )
    assert "CURRENT_EDITOR_MARKER" not in prompt_without_context
    assert "LAYER 4" not in prompt_without_context


def test_context_receipt_is_not_serialised_to_the_provider():
    from mu.session.messages import build_messages_from_history

    messages = build_messages_from_history(
        [],
        {
            "role": "user",
            "parts": [
                {
                    "type": "editor_context_receipt",
                    "receipt": {"live": {"path": "main.py"}},
                },
                {
                    "type": "editor_tool_receipt",
                    "expired": True,
                    "tools": [{"tool_name": "nvim_get_buffer"}],
                },
                {"type": "text", "text": "Explain this"},
            ],
        },
    )

    assert len(messages) == 1
    assert [(part.type, part.text) for part in messages[0].parts] == [
        ("text", "Explain this")
    ]


def test_editor_tool_observations_expire_to_content_free_receipts():
    marker = "STALE_EDITOR_TOOL_SOURCE"

    class Manager:
        history = [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Change this"}],
            },
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "tool_name": "nvim_propose_edit",
                        "tool_args": {
                            "file_path": "main.py",
                            "new_content": marker,
                            "expected_changedtick": 9,
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool_result",
                        "tool_name": "nvim_get_buffer",
                        "tool_result": {"content": marker},
                        "cache_key": "should-disappear",
                    }
                ],
            },
        ]
        conversation_summary = (
            "Keep intent\n- tool_result:nvim_get_buffer => " + marker
        )

    class Session:
        session_manager = Manager()

    assert sanitise_editor_tool_history(Session()) == 3
    encoded = json.dumps(Session.session_manager.history)
    assert marker not in encoded
    assert "should-disappear" not in encoded
    assert len(Session.session_manager.history) == 1
    receipt = Session.session_manager.history[0]["parts"][1]
    assert receipt["type"] == "editor_tool_receipt"
    assert receipt["count"] == 1
    assert receipt["tools"][0]["args"] == {
        "file_path": "main.py",
        "expected_changedtick": 9,
        "expired": True,
    }
    assert Session.session_manager.conversation_summary == "Keep intent"


def test_normalise_rejects_wrong_version_and_oversized_payload():
    try:
        normalise_editor_context({"version": 1})
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("version 1 context should be rejected")

    try:
        normalise_editor_context({"version": 2, "junk": "x" * 130_000})
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("oversized context should be rejected")


def test_normalise_recomputes_budget_and_reports_server_truncation():
    raw = sample_context()
    raw["live"]["viewport"]["content"] = "x" * 65_000
    raw["budget"] = {"included_chars": 1, "approx_tokens": 1}

    context = normalise_editor_context(raw)

    assert len(context["live"]["viewport"]["content"]) == 64_000
    assert context["live"]["viewport"]["truncated"] is True
    assert context["budget"]["included_chars"] > 64_000
    assert context["budget"]["approx_tokens"] > 16_000
    assert context["budget"]["truncated"] is True
    assert context["budget"]["excluded_count"] >= 1


def test_legacy_context_sanitiser_preserves_user_prose_only():
    class Manager:
        history = [
            {
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "Fix this\n\n## MUCLI editor context\nSECRET_OLD_CODE",
                    }
                ],
            }
        ]
        conversation_summary = (
            "Useful intent\n\n## MUCLI editor context\nSECRET_OLD_SUMMARY"
        )

    class Session:
        session_manager = Manager()

    assert sanitise_legacy_editor_history(Session()) == 2
    assert Manager.history[0]["parts"][0]["text"] == "Fix this"
    assert Session.session_manager.conversation_summary == "Useful intent"
    assert (
        strip_legacy_editor_context_text(
            "Compacted intent ## MUCLI editor context Workspace: project"
        )
        == "Compacted intent"
    )
