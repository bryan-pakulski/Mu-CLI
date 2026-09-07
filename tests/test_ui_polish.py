"""Regression checks for terminal rendering and revalidated GUI assets."""

import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient
from rich.cells import cell_len
from rich.console import Console

from mu.gui.app import _RevalidatedStaticFiles
from mu.ui import render
from mu.ui.rich_ui import _GenerationLive


@pytest.mark.parametrize("width", [24, 40, 80])
def test_terminal_code_panels_fit_and_preserve_content(monkeypatch, width):
    console = Console(file=io.StringIO(), width=width, record=True, color_system=None)
    monkeypatch.setattr(render, "console", console)
    render.render_response(
        "Before\n\n```python\nprint('hello')\n# " + "x" * 100 + "\n```\n\n"
        "<new_file path='example.py'>new_file_content</new_file>\n\nAfter"
    )
    output = console.export_text()
    assert "Before" in output and "After" in output
    assert "new_file_content" in output
    assert output.count("x") >= 100
    assert "```" not in output
    assert all(cell_len(line) <= width for line in output.splitlines())


def test_stream_deltas_do_not_rebuild_the_transcript(monkeypatch):
    ui = SimpleNamespace(console=Console(file=io.StringIO()), _gen_live=None)
    live = _GenerationLive(ui, "Working")
    rebuilds = []
    monkeypatch.setattr(live, "_render", lambda **kw: rebuilds.append(True))
    for _ in range(2000):
        live.append_text("a")
        live.append_thinking("b")
    live.note_tool_call("read_file")
    live.update("Finishing")
    assert not rebuilds
    assert "".join(live._text_buf) == "a" * 2000
    assert "".join(live._thinking_buf) == "b" * 2000


def test_static_assets_revalidate_and_pick_up_changes(tmp_path):
    asset = tmp_path / "app.js"
    asset.write_text("const version = 1;" + " " * 2000)
    app = FastAPI()
    app.mount(
        "/static",
        GZipMiddleware(_RevalidatedStaticFiles(directory=tmp_path), minimum_size=1024),
    )
    with TestClient(app) as client:
        first = client.get("/static/app.js")
        assert first.status_code == 200
        assert first.headers["content-encoding"] == "gzip"
        assert int(first.headers["content-length"]) < len(first.content)
        assert first.headers["cache-control"] == "no-cache"
        cached = client.get(
            "/static/app.js", headers={"If-None-Match": first.headers["etag"]}
        )
        assert cached.status_code == 304
        assert cached.content == b""
        asset.write_text("const version = 2; // changed")
        updated = client.get(
            "/static/app.js", headers={"If-None-Match": first.headers["etag"]}
        )
        assert updated.status_code == 200
        assert "version = 2" in updated.text
