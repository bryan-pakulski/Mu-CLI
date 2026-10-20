"""Editor integration contracts: context safety, provider isolation, and transport."""

import json
import subprocess
import sys
import threading
import time

import pytest

from mu.editor.context import MAX_BUFFER_CHARS, Workspace
from mu.editor.service import EditorService
from mu.editor.__main__ import Lane
from providers.base import StreamEvent


def params(**overrides):
    return dict(
        {
            "path": "main.py",
            "text": "def hello():\n    ",
            "row": 1,
            "col": 4,
            "provider": {"name": "fake", "model": "fast"},
        },
        **overrides,
    )


class Provider:
    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.requests = []
        self.closed = 0

    def stream(self, messages, **kwargs):
        self.requests.append((list(messages), kwargs))
        try:
            yield from next(self.rounds)
        finally:
            self.closed += 1


def service(tmp_path, provider):
    return EditorService(str(tmp_path), lambda *a, **k: provider)


def test_utf8_cursor_and_unsaved_buffers(tmp_path):
    ws = Workspace(str(tmp_path))
    result = ws.snapshot(
        params(
            text="é = 2\nreturn é",
            row=0,
            col=2,
            buffers=[{"path": "other.py", "text": "unsaved"}],
        )
    )
    assert result["prefix"] == "é"
    assert result["suffix"].startswith(" = 2")
    assert result["buffers"] == [{"path": "other.py", "text": "unsaved"}]
    with pytest.raises(ValueError, match="UTF-8"):
        ws.snapshot(params(text="é", row=0, col=1))


def test_context_rejects_secret_escape_and_symlink(tmp_path):
    ws = Workspace(str(tmp_path))
    outside = tmp_path.parent / "outside-editor.py"
    (tmp_path / "link.py").symlink_to(outside)
    for name in ("../outside-editor.py", "link.py", ".env", "id_rsa", ".git/config"):
        with pytest.raises(ValueError):
            ws.snapshot(params(path=name))
    result = ws.snapshot(params(buffers=[{"path": ".env", "text": "secret"}]))
    assert result["buffers"] == []


def test_exclusions_apply_to_buffers_reads_and_search(tmp_path):
    (tmp_path / ".mucliignore").write_text("private/\n*.private.py\n")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "notes.py").write_text("needle")
    (tmp_path / "ok.py").write_text("def needle():\n    return 1")
    (tmp_path / "hidden.private.py").write_text("needle")
    ws = Workspace(str(tmp_path))
    with pytest.raises(ValueError, match="excluded"):
        ws.snapshot(params(path="private/notes.py"))
    with pytest.raises(ValueError):
        ws.read("hidden.private.py")
    assert [r["path"] for r in ws.search("needle")] == ["ok.py"]


def test_gitignore_and_binary_files_excluded_from_search(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "ignored.py").write_text("needle")
    (tmp_path / "binary.dat").write_bytes(b"needle\0")
    (tmp_path / "ok.py").write_text("needle")
    assert [r["path"] for r in Workspace(str(tmp_path)).search("needle")] == ["ok.py"]


def test_secret_scrubbing_and_size_limits(tmp_path):
    token = "ghp_" + "a" * 36
    ws = Workspace(str(tmp_path))
    assert token not in json.dumps(ws.snapshot(params(text=token, row=0, col=0)))
    with pytest.raises(ValueError, match="secret material"):
        ws.snapshot(params(text=token, row=0, col=10))
    with pytest.raises(ValueError, match="size"):
        ws.snapshot(params(text="x" * (MAX_BUFFER_CHARS + 1)))
    (tmp_path / "big.py").write_text("x" * 200_001)
    with pytest.raises(ValueError, match="size"):
        ws.read("big.py")


def test_search_and_reads_use_unsaved_active_buffer(tmp_path):
    (tmp_path / "main.py").write_text("old_disk_value")
    ws = Workspace(str(tmp_path))
    ws.snapshot(params(text="new_unsaved_value", row=0, col=0))
    assert ws.read("main.py")["text"] == "1: new_unsaved_value"
    assert ws.search("new_unsaved_value")[0]["path"] == "main.py"
    assert not ws.search("old_disk_value")
    ws.snapshot(params(path="new.py", text="new_file_value", row=0, col=0))
    assert ws.search("new_file_value")[0]["path"] == "new.py"


def test_excluded_symlink_alias_cannot_bypass_ignore(tmp_path):
    (tmp_path / ".mucliignore").write_text("private.py\n")
    (tmp_path / "public.py").write_text("data")
    (tmp_path / "private.py").symlink_to(tmp_path / "public.py")
    with pytest.raises(ValueError, match="excluded"):
        Workspace(str(tmp_path)).snapshot(params(path="private.py"))


def test_analysis_follows_read_tools_and_reports_sources(tmp_path):
    (tmp_path / "main.py").write_text("def hello():\n    return 1\n")
    provider = Provider(
        [
            [
                StreamEvent(
                    "tool_call_complete",
                    tool_name="read",
                    tool_args={"path": "main.py", "start": 1},
                    tool_call_id="r",
                )
            ],
            [StreamEvent("text_delta", text="hello returns 1 (main.py:2).")],
        ]
    )
    result = service(tmp_path, provider).run(
        "analyze",
        params(instruction="hello", text="def hello():\n    return 1\n"),
        threading.Event(),
    )
    assert result["sources"][0]["path"] == "main.py"
    assert result["text"].startswith("hello returns")
    history = provider.requests[1][0]
    assert history[-1].role == "tool"
    assert history[-1].parts[0].tool_call_id == "r"
    assert "return 1" in history[-1].parts[0].tool_result["text"]


def test_analysis_cannot_write_or_read_outside_workspace(tmp_path):
    provider = Provider(
        [
            [
                StreamEvent(
                    "tool_call_complete",
                    tool_name="write_file",
                    tool_args={"path": "new.py"},
                    tool_call_id="w",
                ),
                StreamEvent(
                    "tool_call_complete",
                    tool_name="read",
                    tool_args={"path": "../secret"},
                    tool_call_id="r",
                ),
            ],
            [StreamEvent("text_delta", text="No access")],
        ]
    )
    service(tmp_path, provider).run("analyze", params(), threading.Event())
    assert not (tmp_path / "new.py").exists()
    assert all(
        "error" in part.tool_result for part in provider.requests[1][0][-1].parts
    )


def test_edit_never_writes_and_preserves_indentation(tmp_path):
    path = tmp_path / "main.py"
    path.write_text("original")
    provider = Provider(
        [[StreamEvent("text_delta", text="```python\n    return 2\n```")]]
    )
    result = service(tmp_path, provider).run(
        "edit", params(selection={"start": 1, "end": 2}), threading.Event()
    )
    assert result["text"] == "    return 2"
    assert path.read_text() == "original"


def test_oversized_edit_is_not_offered_for_approval(tmp_path):
    provider = Provider([[StreamEvent("text_delta", text="x" * 16001)]])
    with pytest.raises(ValueError, match="Replacement exceeds"):
        service(tmp_path, provider).run(
            "edit", params(selection={"start": 1, "end": 2}), threading.Event()
        )
    assert provider.closed == 1


def test_provider_errors_surface_to_caller(tmp_path):
    provider = Provider([[StreamEvent("error", text="offline")]])
    editor = service(tmp_path, provider)
    with pytest.raises(RuntimeError, match="offline"):
        editor.run("analyze", params(instruction="why?"), threading.Event())


def test_large_buffer_analysis_keeps_prompt_bounded(tmp_path):
    provider = Provider([[StreamEvent("text_delta", text="value")]])
    source = "# preceding code\n" * 10000 + "result = "
    service(tmp_path, provider).run(
        "analyze",
        params(text=source, row=10000, col=9, instruction="what is result?"),
        threading.Event(),
    )
    payload = provider.requests[0][0][-1].parts[0].text
    assert len(payload) < 13000
    context = json.loads(payload)["context"]
    # The analysis lane trims the prefix but must keep the cursor line in focus.
    assert context["focus"]["text"].endswith("result = ")


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "-----BEGIN PRIVATE KEY-----\nsecret_material\n-----END PRIVATE KEY-----",
        "aws_secret_access_key = " + "a" * 40,
    ],
)
def test_streamed_answer_never_emits_partial_secrets(tmp_path, secret):
    emitted = []
    chunks = [StreamEvent("text_delta", text=char) for char in secret + "\n"]
    result = service(tmp_path, Provider([chunks])).run(
        "analyze", params(instruction="show it"), threading.Event(), emitted.append
    )
    assert secret not in result["text"]
    assert "REDACTED" in result["text"]
    assert all(
        "aaaa" not in json.dumps(event) and "secret_material" not in json.dumps(event)
        for event in emitted
    )


def test_cancelled_request_closes_provider_stream(tmp_path):
    cancel = threading.Event()

    class CancellingProvider(Provider):
        def stream(self, *args, **kwargs):
            cancel.set()
            yield from super().stream(*args, **kwargs)

    provider = CancellingProvider([[StreamEvent("text_delta", text="stale")]])
    assert service(tmp_path, provider).run("analyze", params(instruction="what?"), cancel) == {}
    assert provider.closed == 1


def test_latest_pending_request_and_independent_lanes():
    entered, release, completed = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    seen, sent = [], []

    class FakeService:
        def run(self, method, params, cancel, emit):
            seen.append(params["number"])
            if params["number"] == 1:
                entered.set()
                assert release.wait(3)
            return {"number": params["number"]}

    def send(message):
        sent.append(message)
        if message.get("id") == 4:
            completed.set()

    lane, analysis = Lane(FakeService(), send), Lane(FakeService(), send)
    try:
        lane.submit({"id": 1, "method": "analyze", "params": {"number": 1}})
        assert entered.wait(3)
        lane.submit({"id": 2, "method": "analyze", "params": {"number": 2}})
        lane.submit({"id": 3, "method": "analyze", "params": {"number": 3}})
        analysis.submit({"id": 4, "method": "analyze", "params": {"number": 4}})
        assert completed.wait(3), "Analysis must not wait on completion"
        release.set()
        deadline = time.monotonic() + 3
        while not any(m.get("id") == 3 for m in sent) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert seen == [1, 4, 3]
        assert not any(m["id"] == 1 for m in sent)
        assert next(m for m in sent if m["id"] == 2)["error"] == "Request superseded"
    finally:
        release.set()
        lane.close()
        analysis.close()


def test_real_stdio_protocol_handles_bad_requests(tmp_path):
    requests = (
        "\n".join(["[]", '{"id":1,"method":"unknown"}', '{"id":2,"method":"ping"}'])
        + "\n"
    )
    process = subprocess.run(
        [sys.executable, "-m", "mu.editor", "--workspace", str(tmp_path)],
        input=requests,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    messages = [json.loads(line) for line in process.stdout.splitlines()]
    assert messages[0]["error"] == "Expected a JSON object"
    assert messages[1]["id"] == 1 and "error" in messages[1]
    assert messages[2]["result"]["protocol"] == 1
