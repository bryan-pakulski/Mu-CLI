
from __future__ import annotations

import json
import os
import zipfile

from mu.attachment import AttachmentRegistry
from mu.attachment.extract import read_chunk, search_text


def test_registry_persists_and_deduplicates(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    source = tmp_path / "notes.txt"
    source.write_text("alpha bravo charlie", encoding="utf-8")
    registry = AttachmentRegistry(str(session_dir))

    first = registry.add("notes.txt", str(source), "text/plain")
    second = registry.add("notes.txt", str(source), "text/plain")

    assert first["attachment_id"] == second["attachment_id"]
    assert second["deduplicated"] is True
    assert registry.get(first["attachment_id"])["sha256"] == first["sha256"]
    assert os.path.isfile(registry.resolve_path(first["attachment_id"]))


def test_registry_removal_is_confined(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    source = tmp_path / "input.log"
    source.write_text("failure at line 42", encoding="utf-8")
    registry = AttachmentRegistry(str(session_dir))
    item = registry.add("../input.log", str(source), "text/plain")

    assert item["name"] == "input.log"
    assert registry.remove(item["attachment_id"]) is True
    assert source.exists()
    assert registry.get(item["attachment_id"]) is None


def test_text_read_and_search(tmp_path):
    source = tmp_path / "report.md"
    source.write_text("Header\n\nImportant telemetry mismatch near frame 900.", encoding="utf-8")
    result = read_chunk(str(source), "text/markdown", offset=0, max_chars=500)
    matches = search_text(result["text"], "telemetry")

    assert result["format"] == "text"
    assert matches and "telemetry" in matches[0]["snippet"]


def test_docx_extraction_without_external_dependency(tmp_path):
    source = tmp_path / "sample.docx"
    xml = """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
    <w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">
      <w:body><w:p><w:r><w:t>Attachment registry works</w:t></w:r></w:p></w:body>
    </w:document>"""
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", xml)
    result = read_chunk(
        str(source),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "Attachment registry works" in result["text"]
