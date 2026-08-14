import asyncio
from types import SimpleNamespace

# MUCLI_VISUALIZATION_TIMELINE_V2
from mu.artifact import ArtifactRegistry
from mu.artifact.history import (
    extract_visualization,
    match_visualization_reference,
    merge_registry_descriptor,
)
import mu.gui.routers.sessions as sessions_router
from mu.gui.routers.sessions import _visualization_registry_anchors
from mu.tools.artifact.handlers import _visualization_timeline_anchor


ARTIFACT = {
    "artifact_id": "viz-123",
    "kind": "visualization",
    "name": "map.html",
    "title": "Map",
    "height": 480,
}


def test_extract_visualization_from_nested_json_string():
    value = {
        "result": {
            "content": '{"data":{"artifacts":[{"artifact_id":"viz-123","kind":"visualization","name":"map.html"}]}}'
        }
    }
    result = extract_visualization(value)
    assert result is not None
    assert result["artifact_id"] == "viz-123"


def test_match_registry_artifact_by_reference():
    result = match_visualization_reference(
        "published artifact viz-123",
        [ARTIFACT],
        set(),
    )
    assert result == ARTIFACT


def test_registry_descriptor_refreshes_urls_and_dimensions():
    extracted = {
        "artifact_id": "viz-123",
        "kind": "visualization",
        "name": "old.html",
    }
    registered = {
        **ARTIFACT,
        "view_url": "/view/viz-123",
        "height": 720,
    }
    merged = merge_registry_descriptor(extracted, {"viz-123": registered})
    assert merged["name"] == "map.html"
    assert merged["height"] == 720
    assert merged["view_url"] == "/view/viz-123"


def test_durable_anchor_keeps_exact_publish_position_until_compaction():
    history = [
        {
            "role": "user",
            "timeline_id": "turn-stable",
            "parts": [{"type": "text", "text": "Show the trend"}],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "tool_name": "publish_visualization",
                    "tool_args": {"name": "trend.html"},
                }
            ],
        },
        {
            "role": "tool",
            "parts": [{"type": "tool_result", "tool_name": "publish_visualization"}],
        },
        {"role": "assistant", "parts": [{"type": "text", "text": "Here it is."}]},
    ]
    visualization = {
        **ARTIFACT,
        "timeline_turn_id": "turn-stable",
        "timeline_history_index": 1,
        "timeline_part_index": 0,
    }

    exact, fallback = _visualization_registry_anchors(history, [visualization])

    assert exact[(1, 0)] == [visualization]
    assert fallback == {}


def test_durable_anchor_falls_back_to_stable_turn_after_compaction():
    compacted = [
        {
            "role": "user",
            "timeline_id": "turn-stable",
            "parts": [{"type": "text", "text": "Show the trend"}],
        },
        {"role": "assistant", "parts": [{"type": "text", "text": "Here it is."}]},
    ]
    visualization = {
        **ARTIFACT,
        "timeline_turn_id": "turn-stable",
        "timeline_history_index": 1,
        "timeline_part_index": 0,
    }

    exact, fallback = _visualization_registry_anchors(compacted, [visualization])

    assert exact == {}
    assert fallback[0] == [visualization]


def test_durable_anchor_never_moves_to_a_replacement_conversation():
    replacement = [
        {
            "role": "user",
            "timeline_id": "turn-new",
            "parts": [{"type": "text", "text": "A new conversation"}],
        }
    ]
    visualization = {
        **ARTIFACT,
        "timeline_turn_id": "turn-old",
        "timeline_history_index": 1,
        "timeline_part_index": 0,
    }

    assert _visualization_registry_anchors(replacement, [visualization]) == ({}, {})


def test_publish_handler_captures_stable_turn_and_exact_tool_boundary():
    history = [
        {
            "role": "user",
            "timeline_id": "turn-stable",
            "parts": [{"type": "text", "text": "Show the trend"}],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "tool_name": "publish_visualization",
                    "tool_args": {"name": "trend.html"},
                }
            ],
        },
    ]
    manager = SimpleNamespace(history=history, _active_turn_start_index=0)
    context = SimpleNamespace(session=SimpleNamespace(session_manager=manager))

    assert _visualization_timeline_anchor(context, {"name": "trend.html"}) == (
        "turn-stable",
        1,
        0,
    )


def test_history_endpoint_replays_compacted_visualization_at_original_turn(
    tmp_path, monkeypatch
):
    history_root = tmp_path / "mucli"
    session_dir = history_root / "sessions" / "demo"
    registry = ArtifactRegistry(str(session_dir))
    artifact = registry.add(
        "trend.html",
        content="<!doctype html><p>trend</p>",
        mime_type="text/html",
        kind="visualization",
        timeline_turn_id="turn-stable",
        timeline_history_index=1,
        timeline_part_index=0,
    )
    manager = SimpleNamespace(
        current_session_name="demo",
        history=[
            {
                "role": "user",
                "timeline_id": "turn-stable",
                "parts": [{"type": "text", "text": "Show the trend"}],
            },
            {
                "role": "assistant",
                "parts": [{"type": "text", "text": "Here it is."}],
            },
        ],
    )
    session = SimpleNamespace(session_manager=manager)
    state = SimpleNamespace(session_by_name=lambda _name: session)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    monkeypatch.setattr(sessions_router._config, "HISTORY_DIR", str(history_root))

    response = asyncio.run(
        sessions_router.get_history(
            request,
            session_name="demo",
            limit_turns=None,
            artifact_limit=None,
            before_index=None,
        )
    )

    replayed = [
        part
        for part in response["turns"][0]["parts"]
        if part.get("type") == "visualization"
    ]
    assert replayed[0]["artifact"]["artifact_id"] == artifact["artifact_id"]
    assert response["unplaced_visualizations"] == 0


def test_paginated_history_emits_visualization_only_on_its_anchor_page(
    tmp_path, monkeypatch
):
    history_root = tmp_path / "mucli"
    session_dir = history_root / "sessions" / "demo"
    registry = ArtifactRegistry(str(session_dir))
    artifact = registry.add(
        "trend.html",
        content="<!doctype html><p>trend</p>",
        mime_type="text/html",
        kind="visualization",
        timeline_turn_id="turn-stable",
        timeline_history_index=1,
        timeline_part_index=0,
    )
    descriptor_result = {"artifact": artifact, "artifacts": [artifact]}
    manager = SimpleNamespace(
        current_session_name="demo",
        history=[
            {
                "role": "user",
                "timeline_id": "turn-stable",
                "parts": [{"type": "text", "text": "Show the trend"}],
            },
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "tool_name": "publish_visualization",
                        "tool_args": {"name": "trend.html"},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool_result",
                        "tool_name": "publish_visualization",
                        "tool_result": descriptor_result,
                    }
                ],
            },
            {
                "role": "assistant",
                "parts": [{"type": "text", "text": "Here it is."}],
            },
        ],
    )
    session = SimpleNamespace(session_manager=manager)
    state = SimpleNamespace(session_by_name=lambda _name: session)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    monkeypatch.setattr(sessions_router._config, "HISTORY_DIR", str(history_root))

    recent = asyncio.run(
        sessions_router.get_history(
            request,
            session_name="demo",
            limit_turns=2,
            artifact_limit=None,
            before_index=None,
        )
    )
    older = asyncio.run(
        sessions_router.get_history(
            request,
            session_name="demo",
            limit_turns=2,
            artifact_limit=None,
            before_index=2,
        )
    )

    recent_cards = [
        part
        for turn in recent["turns"]
        for part in turn["parts"]
        if part.get("artifact", {}).get("artifact_id") == artifact["artifact_id"]
    ]
    older_cards = [
        part
        for turn in older["turns"]
        for part in turn["parts"]
        if part.get("artifact", {}).get("artifact_id") == artifact["artifact_id"]
    ]
    assert recent_cards == []
    assert len(older_cards) == 1
    assert older_cards[0]["type"] == "visualization"
