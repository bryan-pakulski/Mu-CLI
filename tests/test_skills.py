"""Pin the skills discovery + injection system.

Skills are declarative agent extensions discovered from
`<root>/SKILL.md` files with YAML frontmatter. They appear in the
system prompt as a labelled AVAILABLE SKILLS block and via the
`/skills` slash command.
"""

import os

from core.session import Session, SessionManager
from mu.skills import (
    Skill,
    _parse_skill_md,
    clear_skill_cache,
    discover_skills,
    get_skill,
    match_trigger,
    render_skills_block,
    render_skills_expanded,
)
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


def _write_skill(dir_path, name, description, body, *, trigger=None):
    skill_dir = dir_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = [f"name: {name}", f"description: {description}"]
    if trigger:
        fm_lines.append(f"trigger: {trigger}")
    content = "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def test_parse_skill_md_extracts_frontmatter_and_body(tmp_path):
    skill_dir = _write_skill(
        tmp_path, "hello-world", "Say hello.", "Body content here.", trigger=r"\bhello\b"
    )
    skill = _parse_skill_md(str(skill_dir / "SKILL.md"))
    assert skill is not None
    assert skill.name == "hello-world"
    assert skill.description == "Say hello."
    assert skill.body == "Body content here."
    assert skill.trigger == r"\bhello\b"
    assert skill.trigger_regex is not None


def test_parse_skill_md_falls_back_to_dir_name_if_name_missing(tmp_path):
    skill_dir = tmp_path / "no-name-key"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: anon\n---\nbody", encoding="utf-8"
    )
    skill = _parse_skill_md(str(skill_dir / "SKILL.md"))
    assert skill is not None
    assert skill.name == "no-name-key"


def test_parse_skill_md_returns_none_without_frontmatter(tmp_path):
    skill_dir = tmp_path / "plain"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("just a markdown file", encoding="utf-8")
    assert _parse_skill_md(str(skill_dir / "SKILL.md")) is None


def test_workspace_skills_shadow_builtins(tmp_path):
    """A skill in `<workspace>/.mu/skills/` with the same name as a builtin
    should override the builtin entry."""
    clear_skill_cache()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_root = workspace / ".mu" / "skills"
    _write_skill(skills_root, "commit-message", "Local override.", "Body.")
    skills = discover_skills([str(workspace)])
    by_name = {s.name: s for s in skills}
    assert "commit-message" in by_name
    assert by_name["commit-message"].description == "Local override."


def test_render_skills_block_compact_index_excludes_bodies():
    s1 = Skill(name="alpha", description="A first skill.", body="A body.", source="/a")
    s2 = Skill(name="beta", description="A second skill.", body="B body.", source="/b")
    block = render_skills_block([s1, s2], budget=10000)
    assert "### AVAILABLE SKILLS" in block
    assert "SKILL: alpha" in block
    assert "SKILL: beta" in block
    # Compact index never inlines bodies.
    assert "A body." not in block
    assert "B body." not in block


def test_render_skills_block_full_mode_includes_bodies():
    s1 = Skill(name="alpha", description="A first skill.", body="A body.", source="/a")
    s2 = Skill(name="beta", description="A second skill.", body="B body.", source="/b")
    block = render_skills_block([s1, s2], budget=10000, mode="full")
    assert "### AVAILABLE SKILLS" in block
    assert "A body." in block
    assert "B body." in block


def test_render_skills_block_respects_budget_in_full_mode():
    huge = "x" * 5000
    skills = [
        Skill(name=f"s{i}", description="d", body=huge, source=f"/s{i}")
        for i in range(5)
    ]
    block = render_skills_block(skills, budget=6000, mode="full")
    assert len(block) <= 6500  # budget + minor header padding
    assert "more skill" in block


def test_render_skills_block_compact_respects_budget():
    skills = [
        Skill(name=f"skill-{i:03d}", description="x" * 100, body="b", source=f"/s{i}")
        for i in range(200)
    ]
    block = render_skills_block(skills, budget=500)
    assert len(block) <= 700
    assert "more skill" in block


def test_render_skills_block_empty_when_no_skills():
    assert render_skills_block([], budget=10000) == ""


def test_render_skills_block_auto_expands_on_trigger_match():
    import re as _re

    s1 = Skill(
        name="alpha",
        description="A first skill.",
        body="A body.",
        source="/a",
        trigger=r"\bhello\b",
        trigger_regex=_re.compile(r"\bhello\b", _re.IGNORECASE),
    )
    s2 = Skill(name="beta", description="Other.", body="B body.", source="/b")
    block = render_skills_block([s1, s2], budget=10000, user_text="hello world")
    assert "### AUTO-EXPANDED SKILLS" in block
    assert "A body." in block  # alpha was triggered
    assert "B body." not in block  # beta stays in index
    assert "SKILL: beta" in block


def test_render_skills_block_no_auto_expand_without_user_text():
    import re as _re

    s1 = Skill(
        name="alpha",
        description="d",
        body="A body.",
        source="/a",
        trigger=r"hello",
        trigger_regex=_re.compile("hello", _re.IGNORECASE),
    )
    block = render_skills_block([s1], budget=10000, user_text=None)
    assert "### AUTO-EXPANDED SKILLS" not in block
    assert "A body." not in block


def test_match_trigger_handles_missing_regex_and_empty_text():
    s = Skill(name="x", description="d", body="b", source="/x")
    assert match_trigger(s, "anything") is False
    import re as _re

    s.trigger_regex = _re.compile("foo", _re.IGNORECASE)
    assert match_trigger(s, "") is False
    assert match_trigger(s, "FOO bar") is True
    assert match_trigger(s, "no match") is False


def test_invalid_trigger_regex_is_rejected_gracefully(tmp_path):
    skill_dir = _write_skill(
        tmp_path, "bad-trigger", "d", "body", trigger="["  # unclosed character class
    )
    skill = _parse_skill_md(str(skill_dir / "SKILL.md"))
    assert skill is not None
    assert skill.trigger == "["
    assert skill.trigger_regex is None  # graceful — skill still loads


def test_catastrophic_backtracking_pattern_rejected(tmp_path):
    skill_dir = _write_skill(
        tmp_path, "bad-shape", "d", "body", trigger="(.+)+x"
    )
    skill = _parse_skill_md(str(skill_dir / "SKILL.md"))
    assert skill is not None
    assert skill.trigger_regex is None


def test_get_skill_lookup(tmp_path):
    clear_skill_cache()
    workspace = tmp_path / "ws"
    skills_root = workspace / ".mu" / "skills"
    _write_skill(skills_root, "findme", "lookup test.", "Body.")
    found = get_skill("findme", [str(workspace)])
    assert found is not None
    assert found.name == "findme"
    assert get_skill("does-not-exist", [str(workspace)]) is None


def test_discover_skills_finds_builtin_commit_message():
    clear_skill_cache()
    skills = discover_skills([])
    names = {s.name for s in skills}
    assert "commit-message" in names


def test_clear_skill_cache_forces_rediscovery(tmp_path, monkeypatch):
    clear_skill_cache()
    workspace = tmp_path / "ws"
    (workspace / ".mu" / "skills").mkdir(parents=True)
    # First scan: empty.
    assert get_skill("late-arrival", [str(workspace)]) is None
    # Drop a new skill on disk, then force reload.
    _write_skill(workspace / ".mu" / "skills", "late-arrival", "d", "body")
    clear_skill_cache()
    assert get_skill("late-arrival", [str(workspace)]) is not None


def test_session_injects_skills_layer_into_system_prompt():
    clear_skill_cache()
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    full = session._inject_hierarchical_context("base prompt")
    # The built-in skills must show up under LAYER 1B.
    assert "LAYER 1B — Installed skills" in full
    assert "SKILL: commit-message" in full


def test_skills_disabled_via_zero_budget():
    clear_skill_cache()
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.variables["skills_max_chars"] = 0
    full = session._inject_hierarchical_context("base prompt")
    assert "LAYER 1B" not in full


def test_session_disabled_skills_filter():
    clear_skill_cache()
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.disabled_skills = ["commit-message"]
    full = session._inject_hierarchical_context("base prompt")
    assert "SKILL: commit-message" not in full


def test_session_full_mode_inlines_bodies():
    clear_skill_cache()
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.variables["skills_mode"] = "full"
    full = session._inject_hierarchical_context("base prompt")
    # In full mode the bundled commit-message skill's body should be inlined.
    # The body mentions "commit" (it's a commit-drafting skill prompt).
    assert "LAYER 1B — Installed skills" in full
    # Should not show "[trigger:" prefix because full mode inlines bodies, not the compact index.
    assert "AUTO-EXPANDED SKILLS" not in full


def test_skills_slash_command_lists_installed_skills():
    """The /skills command must return a non-empty list with the bundled skills."""
    clear_skill_cache()
    from mu.commands.skills import skills_cmd

    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    result = skills_cmd(session, "", allow_prompt=False)
    assert result.ok
    skill_names = {s["name"] for s in result.data["skills"]}
    assert "commit-message" in skill_names
    assert "code-review" in skill_names


def test_skills_slash_command_show_one_by_name():
    clear_skill_cache()
    from mu.commands.skills import skills_cmd

    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    result = skills_cmd(session, "commit-message", allow_prompt=False)
    assert result.ok
    assert result.data["name"] == "commit-message"
    assert os.path.basename(result.data["source"]) == "SKILL.md"


def test_skills_slash_command_reload():
    clear_skill_cache()
    from mu.commands.skills import skills_cmd

    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    result = skills_cmd(session, "reload", allow_prompt=False)
    assert result.ok
    assert "count" in (result.data or {})
    assert result.data["count"] >= 1


def test_skills_slash_command_disable_then_enable():
    clear_skill_cache()
    from mu.commands.skills import skills_cmd

    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    r1 = skills_cmd(session, "disable commit-message", allow_prompt=False)
    assert r1.ok
    assert "commit-message" in session.disabled_skills
    r2 = skills_cmd(session, "enable commit-message", allow_prompt=False)
    assert r2.ok
    assert "commit-message" not in session.disabled_skills


def test_skills_slash_command_disable_unknown_fails():
    from mu.commands.skills import skills_cmd

    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    result = skills_cmd(session, "disable does-not-exist", allow_prompt=False)
    assert not result.ok


def test_invoke_skill_tool_handler_returns_body():
    from core.tools import TOOL_HANDLERS, build_tool_context

    ctx = build_tool_context(folder_context=type("FC", (), {"folders": []})())
    handler = TOOL_HANDLERS["invoke_skill"]
    result = handler({"name": "commit-message"}, ctx)
    assert "SKILL: commit-message" in result
    # The bundled commit-message skill body mentions commits.
    assert "commit" in result.lower()


def test_invoke_skill_tool_handler_unknown_name_errors():
    from core.tools import TOOL_HANDLERS, build_tool_context

    ctx = build_tool_context(folder_context=type("FC", (), {"folders": []})())
    handler = TOOL_HANDLERS["invoke_skill"]
    result = handler({"name": "no-such-skill"}, ctx)
    assert result.lower().startswith("error:")


def test_render_skills_expanded_includes_header_and_body():
    s = Skill(name="alpha", description="d", body="big body here", source="/a")
    out = render_skills_expanded(s)
    assert "SKILL: alpha" in out
    assert "big body here" in out
