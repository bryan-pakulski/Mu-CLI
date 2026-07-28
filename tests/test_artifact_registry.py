import json
import os

import pytest

from mu.artifact.registry import ArtifactError, ArtifactRegistry


def test_inline_artifact_round_trip(tmp_path):
    registry = ArtifactRegistry(str(tmp_path / "sessions" / "demo"))
    item = registry.add("report.md", content="# hello", mime_type="text/markdown")
    assert item["name"] == "report.md"
    assert item["download_url"].endswith(f"/{item['artifact_id']}/download")
    assert open(registry.resolve_path(item["artifact_id"]), encoding="utf-8").read() == "# hello"
    assert registry.list()[0]["artifact_id"] == item["artifact_id"]
    assert registry.remove(item["artifact_id"])
    assert registry.list() == []


def test_file_artifact_is_copied_and_source_is_unchanged(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"abc")
    registry = ArtifactRegistry(str(tmp_path / "sessions" / "demo"))
    item = registry.add("copy.bin", source_path=str(source))
    assert open(registry.resolve_path(item["artifact_id"]), "rb").read() == b"abc"
    assert source.read_bytes() == b"abc"


def test_requires_exactly_one_payload(tmp_path):
    registry = ArtifactRegistry(str(tmp_path / "demo"))
    with pytest.raises(ArtifactError):
        registry.add("x.txt")
    with pytest.raises(ArtifactError):
        registry.add("x.txt", source_path=__file__, content="x")


def test_size_limit_and_name_sanitization(tmp_path):
    registry = ArtifactRegistry(str(tmp_path / "demo"), max_bytes=2)
    with pytest.raises(ArtifactError):
        registry.add("x.txt", content="abc")
    item = ArtifactRegistry(str(tmp_path / "other")).add("../../safe.txt", content="x")
    assert item["name"] == "safe.txt"
