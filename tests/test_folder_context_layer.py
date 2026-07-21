"""Tests for the L1C workspace-context layer + diff budget enforcement.

Pins the fix for the long-horizon L0 system-prompt bloat: the workspace
file tree + per-file diffs used to be appended raw to the system-prompt
base (L0), where they grew unbounded and hid from layer accounting. They
now live in a budgeted L1C layer (`folder_context_max_chars`) with
drop-oldest eviction on the diffs.
"""

from providers.base import LLMProvider, ProviderResponse

from mu.workspace.folder_context import FolderContext


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(
            text="ok", parts=[], input_tokens=0, output_tokens=0, total_tokens=0
        )

    def upload_file(self, file_path, mime_type):
        return None


def _make_session():
    from mu.session.session import Session, SessionManager

    sm = SessionManager()
    return Session(_DummyProvider(), False, "you are a helpful assistant", sm)


# ----------------------------------------------------------- diff budget


def test_get_context_diff_xml_enforces_budget_drop_oldest(tmp_path):
    """When the cumulative diff exceeds ``max_chars``, the output is
    truncated to fit and the OLDEST entries (front of walk order) are
    dropped so the most recent changes stay visible."""
    ctx = FolderContext()
    # Several files, each large enough that the combined diff overflows a
    # small budget. Names sort so walk order is stable.
    for name in ["a.txt", "b.txt", "c.txt", "d.txt"]:
        (tmp_path / name).write_text("original\n")
    ctx.add_folder(str(tmp_path))

    # Mutate every file so each produces a diff.
    for name in ["a.txt", "b.txt", "c.txt", "d.txt"]:
        (tmp_path / name).write_text("changed " + name + "\n")

    out = ctx.get_context_diff_xml(max_chars=400)
    assert len(out) <= 400, f"diff XML {len(out)} chars exceeded the 400 budget"
    # Truncation marker must be present (we dropped some entries).
    assert "diffs truncated" in out
    # The wrapper is preserved.
    assert out.startswith("<folder_context_diffs>")
    assert out.endswith("</folder_context_diffs>")


def test_get_context_diff_xml_under_budget_is_untruncated(tmp_path):
    """A small diff under the budget is emitted in full with no marker."""
    ctx = FolderContext()
    (tmp_path / "only.txt").write_text("original\n")
    ctx.add_folder(str(tmp_path))
    (tmp_path / "only.txt").write_text("changed\n")

    out = ctx.get_context_diff_xml(max_chars=8192)
    assert "diffs truncated" not in out
    assert "only.txt" in out


def test_get_context_diff_xml_no_changes_returns_empty(tmp_path):
    ctx = FolderContext()
    (tmp_path / "stable.txt").write_text("same\n")
    ctx.add_folder(str(tmp_path))
    assert ctx.get_context_diff_xml(max_chars=8192) == ""


# ----------------------------------------------------------- tree_only (#2)


def test_get_initial_context_xml_tree_only_omits_contents(tmp_path):
    """tree_only (the default) emits the file tree but NOT file contents —
    the root cause of the 787k L0 bloat was 50 full file bodies in the
    system prompt every iteration."""
    (tmp_path / "main.py").write_text("SECRET_CONTENT_SHOULD_NOT_APPEAR\n")
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    out = ctx.get_initial_context_xml()  # default tree_only=True
    assert "main.py" in out
    assert "SECRET_CONTENT_SHOULD_NOT_APPEAR" not in out
    assert "<initial_folder_context>" in out


# ----------------------------------------------------------- _build_folder_context_block


def test_build_folder_context_block_empty_when_no_folders():
    session = _make_session()
    assert session.folder_context.folders == []
    assert session._build_folder_context_block() == ""


def test_build_folder_context_block_is_tree_only(tmp_path):
    """L1C is tree-only: the file tree is included, per-file change diffs
    are NOT. Diffs were dropped (not trimmed) from the system prompt
    because drop-oldest eviction risked silently discarding relevant
    changes — the model reads file contents on demand instead."""
    session = _make_session()
    (tmp_path / "main.py").write_text("print('hello')\n")
    session.folder_context.add_folder(str(tmp_path))
    (tmp_path / "main.py").write_text("print('changed')\n")

    block = session._build_folder_context_block()
    assert "<initial_folder_context>" in block  # tree
    assert "main.py" in block
    # No diffs injected into the system prompt.
    assert "<folder_context_diffs>" not in block
    assert "FILE CHANGE" not in block


def test_build_folder_context_block_respects_budget(tmp_path):
    """The tree-only block must not exceed folder_context_max_chars — even
    for a workspace with enough tracked files that the path list itself
    overflows a small budget (truncated with a marker, tag preserved)."""
    import pathlib

    session = _make_session()
    # Many files with long-ish relative paths so the tree (paths only)
    # exceeds a small char budget.
    sub = tmp_path / "deeply" / "nested" / "pkg_dir"
    sub.mkdir(parents=True)
    for i in range(60):
        (sub / f"module_with_a_quite_long_name_{i:03d}.py").write_text("x\n")
    session.folder_context.add_folder(str(tmp_path))

    session.variables["folder_context_max_chars"] = 600
    block = session._build_folder_context_block()
    assert len(block) <= 600, (
        f"folder context block {len(block)} chars exceeded the 600-char budget"
    )
    assert "file tree truncated" in block
    assert block.rstrip().endswith("</initial_folder_context>")


# ----------------------------------------------------------- L1C layer injection


def test_inject_includes_l1c_layer_when_folder_context_present(tmp_path):
    session = _make_session()
    (tmp_path / "main.py").write_text("print('hello')\n")
    session.folder_context.add_folder(str(tmp_path))

    out = session._inject_hierarchical_context("base persona")
    assert "LAYER 1C" in out
    assert "tree-only, no diffs" in out


def test_inject_omits_l1c_when_no_folder_context():
    session = _make_session()
    out = session._inject_hierarchical_context("base persona")
    assert "LAYER 1C" not in out


def test_inject_reuses_cached_folder_context(tmp_path):
    """Passing cached_folder_context bypasses the session rebuild, so a
    sentinel value surfaces verbatim (the loop caches per turn)."""
    session = _make_session()
    (tmp_path / "main.py").write_text("print('hello')\n")
    session.folder_context.add_folder(str(tmp_path))

    sentinel = "SENTinel_FOLDER_CONTEXT_BLOCK_xyz"
    out = session._inject_hierarchical_context(
        "base", cached_folder_context=sentinel
    )
    assert sentinel in out
    assert "LAYER 1C" in out


# ----------------------------------------------------------- accounting


def test_collect_context_layers_includes_l1c_row(tmp_path):
    from utils.runtime_metrics import collect_context_layers

    session = _make_session()
    (tmp_path / "main.py").write_text("print('hello')\n")
    session.folder_context.add_folder(str(tmp_path))

    layers = collect_context_layers(session)
    ids = [layer["layer"] for layer in layers]
    assert "L1C" in ids
    l1c = next(layer for layer in layers if layer["layer"] == "L1C")
    assert l1c["current"] >= 0
    # maximum tracks the configured char budget (token-converted).
    assert l1c["maximum"] > 0


def test_l1c_budget_var_routed_by_set_layer(tmp_path):
    """`/set layer L1C <tokens>` must land in folder_context_max_chars."""
    import mu.commands as mc

    session = _make_session()
    result = mc.dispatch(session, "/set layer L1C 1000", allow_prompt=False)
    assert result.ok
    # 1000 tokens → 4000 chars
    assert session.variables["folder_context_max_chars"] == 4000