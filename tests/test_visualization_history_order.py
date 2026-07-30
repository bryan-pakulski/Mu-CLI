# MUCLI_VISUALIZATION_TIMELINE_V2
from mu.artifact.history import (
    extract_visualization,
    match_visualization_reference,
    merge_registry_descriptor,
)


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
