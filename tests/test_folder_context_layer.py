"""Tests for FolderContext diff budget enforcement + tree-only rendering.

The L1C system-prompt layer has been removed — the agent retrieves files
on demand via list_dir/read_file/search_for_string. These tests cover the
FolderContext class methods that remain (get_context_diff_xml for ad-hoc
use, get_initial_context_xml tree_only rendering).
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


# ----------------------------------------------------------- tree_only


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