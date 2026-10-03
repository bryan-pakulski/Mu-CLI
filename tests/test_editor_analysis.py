"""Focused IDE explanations should follow the code, not generic prompt words."""

import json
from pathlib import Path
import threading
from unittest.mock import patch

from mu.editor.context import Workspace
from mu.editor.service import EditorService
from providers.base import StreamEvent

SOURCE = """import sys

def read_prompt(path):
    if path == "-":
        return sys.stdin.read()
    with open(path) as handle:
        return handle.read()

def main(args):
    if args.prompt_file is not None:
        args.prompt = read_prompt(args.prompt_file)

    if args.trace:
        unrelated_trace_launch(args)
"""


class Capture:
    def __init__(self, rounds=None):
        self.calls = []
        self.rounds = iter(rounds) if rounds else None

    def stream(self, messages, **kwargs):
        self.calls.append((list(messages), kwargs))
        if self.rounds is not None:
            yield from next(self.rounds)
        else:
            yield StreamEvent(
                "text_delta",
                text="Loads the prompt from a file or standard input (main.py:3).",
            )


def request(text=SOURCE, **overrides):
    return dict(
        path="main.py",
        text=text,
        row=9,
        col=4,
        filetype="python",
        provider={"name": "fake", "model": "capture"},
        instruction="Explain this code.",
        action="explain",
        **overrides,
    )


def payload(provider):
    return json.loads(provider.calls[-1][0][-1].parts[0].text)["context"]


def test_explain_focuses_branch_and_helper_not_generic_search(tmp_path):
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "noise.py").write_text(
        'def main():\n    # Explain code cursor dependencies callers design decisions\n    return "BENCHMARK_NOISE"\n'
    )
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "noise.min.js").write_text(
        'function read_prompt(){return "VENDOR_NOISE";}'
    )
    provider = Capture()
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    with patch.object(
        editor.workspace,
        "search",
        side_effect=AssertionError("No prose search for Explain"),
    ):
        result = editor.run("analyze", request(), threading.Event())
    context = payload(provider)
    assert context["focus"]["line"] == 10
    assert context["focus"]["end_line"] == 11
    assert "read_prompt(args.prompt_file)" in context["focus"]["text"]
    assert "unrelated_trace_launch" not in json.dumps(context)
    assert any(
        "return sys.stdin.read()" in source["text"] for source in context["sources"]
    )
    assert {source["path"] for source in result["sources"]} == {"main.py"}
    assert [source["line"] for source in result["cited_sources"]] == [3]
    assert result["context"]["context_tokens"] < 1000


def test_visual_selection_is_the_actual_explanation_target(tmp_path):
    provider = Capture()
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    editor.run(
        "analyze", request(selection={"start": 12, "end": 14}), threading.Event()
    )
    focus = payload(provider)["focus"]
    assert focus["line"] == 13 and focus["end_line"] == 14
    assert "unrelated_trace_launch(args)" in focus["text"]
    assert "read_prompt" not in focus["text"]


def test_import_alias_resolves_correct_module_and_unsaved_definition(tmp_path):
    (tmp_path / "launcher.py").write_text(
        'def launch_gui(args):\n    return "OLD_DISK"\n'
    )
    (tmp_path / "other.py").write_text(
        'def launch_gui(args):\n    return "UNRELATED_MODULE"\n'
    )
    provider = Capture()
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    params = request(
        "from launcher import launch_gui as start_ui\n\ndef main(args):\n    start_ui(args)\n",
        buffers=[
            {
                "path": "launcher.py",
                "text": 'def launch_gui(args):\n    return "UNSAVED_LAUNCH"\n',
            }
        ],
    )
    params.update(row=3, col=4)
    editor.run("analyze", params, threading.Event())
    sources = payload(provider)["sources"]
    assert any(
        source["path"] == "launcher.py" and "UNSAVED_LAUNCH" in source["text"]
        for source in sources
    )
    assert "OLD_DISK" not in json.dumps(payload(provider))
    paths = [source["path"] for source in sources]
    assert "launcher.py" in paths
    assert "other.py" not in paths


def test_fresh_explain_does_not_inherit_old_answer_but_followup_knows_location(
    tmp_path,
):
    provider = Capture()
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    old = request()
    old.update(action="analyze", instruction="OLD_SUBJECT")
    editor.run("analyze", old, threading.Event())
    editor.run("analyze", request(), threading.Event())
    assert len(provider.calls[-1][0]) == 1
    assert "OLD_SUBJECT" not in provider.calls[-1][0][0].parts[0].text
    followup = request()
    followup.update(
        action="analyze", instruction="What happens when that file is missing?"
    )
    editor.run("analyze", followup, threading.Event())
    assert any(
        part.text == "Explain main.py:10-11"
        for message in provider.calls[-1][0]
        for part in message.parts
    )


def test_explain_reuses_repeated_reads_and_finishes_after_two_rounds(tmp_path):
    read = StreamEvent(
        "tool_call_complete",
        tool_name="read",
        tool_call_id="read1",
        tool_args={"path": "main.py", "start": 3, "count": 5},
    )
    provider = Capture(
        [
            [read],
            [read],
            [StreamEvent("text_delta", text="Reads the requested prompt file.")],
        ]
    )
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    ws = editor.workspace
    with patch.object(ws, "read", wraps=ws.read) as observed:
        result = editor.run("analyze", request(), threading.Event())
    assert observed.call_count == 1
    assert len(provider.calls) == 3 and provider.calls[-1][1]["tools"] is None
    assert result["text"] == "Reads the requested prompt file."


def test_symbol_search_uses_boundaries_and_skips_vendor_by_default(tmp_path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "bundle.min.js").write_text(
        "function run_gui() { return 1; }"
    )
    (tmp_path / "correct.py").write_text("def run_gui(): pass\n")
    (tmp_path / "wrong.py").write_text("def run_gui_examples(): pass\n")
    workspace = Workspace(str(tmp_path))
    assert [source["path"] for source in workspace.search("run_gui")] == ["correct.py"]
    assert workspace.read("vendor/bundle.min.js")["text"]
    assert any(
        source["path"] == "vendor/bundle.min.js"
        for source in workspace.search("run_gui vendor/bundle.min.js")
    )


def test_real_reported_cursor_has_only_relevant_definitions(tmp_path):
    # Reproduce the supplied failure without making paid model requests.
    root = Path(__file__).resolve().parents[1]
    text = (root / "mucli.py").read_text()
    (tmp_path / "mucli.py").write_text(text)
    provider = Capture()
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    params = request(text)
    params.update(
        path="mucli.py",
        row=text.splitlines().index("    if args.headless_prompt_file is not None:"),
        col=4,
    )
    editor.run("analyze", params, threading.Event())
    context = payload(provider)
    assert context["focus"]["line"] == params["row"] + 1
    assert "read_headless_prompt_file" in context["focus"]["text"]
    assert any(
        "return sys.stdin.read()" in source["text"] for source in context["sources"]
    )
    assert not any("def main(" in source["text"] for source in context["sources"])
    assert all(
        "run_gui(args, build_session)" not in source["text"]
        for source in context["sources"]
    )


def test_annotated_receiver_retrieves_its_imported_type(tmp_path):
    (tmp_path / "contracts.py").write_text(
        "class Invoice:\n    def total(self):\n        return 42\n"
    )
    (tmp_path / "other.py").write_text(
        'class Unrelated:\n    def total(self):\n        return "wrong API"\n'
    )
    provider = Capture()
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    params = request(
        "from contracts import Invoice\n\ndef price(invoice: Invoice):\n    return invoice.total()\n"
    )
    params.update(row=3, col=4)
    editor.run("analyze", params, threading.Event())
    context = payload(provider)
    assert "from contracts import Invoice" in context["imports"]
    assert any(
        source["path"] == "contracts.py" and "return 42" in source["text"]
        for source in context["sources"]
    )
    assert not any(source["path"] == "other.py" for source in context["sources"])


def test_focus_budget_and_secret_scrubbing_apply_to_unsaved_code(tmp_path):
    token = "ghp_" + "a" * 36
    text = (
        'def example():\n    token = "'
        + token
        + '"\n    message = "'
        + "🙂" * 1800
        + '"\n'
    )
    provider = Capture()
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    params = request(text)
    params.update(row=0, col=0, selection={"start": 0, "end": 3})
    params["provider"]["num_ctx"] = 1024
    result = editor.run("analyze", params, threading.Event())
    assert token not in json.dumps(payload(provider))
    assert result["context"]["context_tokens"] <= 512


def test_vendor_filter_does_not_hide_discovery_limit(tmp_path):
    (tmp_path / "one.py").write_text("def one(): return 1\n")
    editor = EditorService(str(tmp_path), lambda *a, **k: Capture())
    names = ["one.py", *[f"vendor/{i}.js" for i in range(3999)]]
    with patch.object(editor.index.workspace, "files", return_value=names):
        editor.index.refresh()
    assert editor.index.status()["files"] == 1
    assert editor.index.status()[
        "limited"
    ], "Filtering bundles must not imply complete workspace coverage"
