"""Pin the workspace-context-file auto-loader (LAYER 1).

When the user attaches a folder via /folder, any `AGENTS.md`, `CLAUDE.md`,
`MUCLI.md`, or `.mu/CONTEXT.md` it finds at the top level is concatenated
with a provenance header and injected into the system prompt as LAYER 1
— ahead of conversation summary, retrieved snippets, and tool history.

This lets users define harness-aware project conventions once and have
the model honor them on every turn without re-prompting.
"""

from mu.session.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse


class DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(
            text="ok", parts=[], input_tokens=0, output_tokens=0, total_tokens=0
        )

    def upload_file(self, file_path, mime_type):
        return None


def _session():
    sm = SessionManager()
    return Session(DummyProvider("dummy"), False, "system instruction", sm)


def test_no_workspace_files_returns_empty_string(tmp_path):
    """When folders are attached but no context files exist, no LAYER 1."""
    session = _session()
    session.folder_context.add_folder(str(tmp_path))
    assert session._build_workspace_context_files() == ""


def test_agents_md_loaded_with_provenance_header(tmp_path):
    session = _session()
    (tmp_path / "AGENTS.md").write_text(
        "# Project conventions\n- Always use snake_case.\n", encoding="utf-8"
    )
    session.folder_context.add_folder(str(tmp_path))

    out = session._build_workspace_context_files()
    assert "AGENTS.md" in out
    assert str(tmp_path) in out  # provenance header includes source folder
    assert "Always use snake_case." in out


def test_multiple_files_concatenated_in_order(tmp_path):
    """Default order is AGENTS.md, CLAUDE.md, MUCLI.md, .mu/CONTEXT.md."""
    session = _session()
    (tmp_path / "AGENTS.md").write_text("AAA-MARKER", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("CCC-MARKER", encoding="utf-8")
    session.folder_context.add_folder(str(tmp_path))

    out = session._build_workspace_context_files()
    assert "AAA-MARKER" in out
    assert "CCC-MARKER" in out
    assert out.index("AAA-MARKER") < out.index("CCC-MARKER")


def test_nested_mu_context_file_loaded(tmp_path):
    """.mu/CONTEXT.md (subdirectory variant) is honored when present."""
    session = _session()
    (tmp_path / ".mu").mkdir()
    (tmp_path / ".mu" / "CONTEXT.md").write_text("nested-marker", encoding="utf-8")
    session.folder_context.add_folder(str(tmp_path))

    out = session._build_workspace_context_files()
    assert "nested-marker" in out


def test_budget_truncates_oversized_blocks(tmp_path):
    session = _session()
    huge = "x" * 50000
    (tmp_path / "AGENTS.md").write_text(huge, encoding="utf-8")
    session.folder_context.add_folder(str(tmp_path))
    session.variables["workspace_context_max_chars"] = 500

    out = session._build_workspace_context_files()
    assert len(out) <= 600  # budget + header slack
    assert "[truncated]" in out


def test_disabled_via_empty_filename_list(tmp_path):
    session = _session()
    (tmp_path / "AGENTS.md").write_text("ignored", encoding="utf-8")
    session.folder_context.add_folder(str(tmp_path))
    session.variables["workspace_context_files"] = ""

    assert session._build_workspace_context_files() == ""


def test_layer_1_appears_in_hierarchical_context(tmp_path):
    """The injected system prompt should label this block as LAYER 1 so
    the model has a clear precedence ordering vs LAYER 2-5."""
    session = _session()
    (tmp_path / "AGENTS.md").write_text("project-rules-here", encoding="utf-8")
    session.folder_context.add_folder(str(tmp_path))

    full = session._inject_hierarchical_context("base system prompt")
    assert "LAYER 1 — Workspace context files" in full
    assert "project-rules-here" in full
    # And LAYER 5 (current turn) still anchors the bottom.
    assert "LAYER 5 — Current turn" in full
    # LAYER 1 must precede LAYER 5.
    assert full.index("LAYER 1") < full.index("LAYER 5")


def test_unknown_files_are_skipped(tmp_path):
    session = _session()
    (tmp_path / "README.md").write_text("not a context file", encoding="utf-8")
    session.folder_context.add_folder(str(tmp_path))

    assert session._build_workspace_context_files() == ""
