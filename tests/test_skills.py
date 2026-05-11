"""Pin the skills discovery + injection system.

Skills are declarative agent extensions discovered from
`<root>/SKILL.md` files with YAML frontmatter. They appear in the
system prompt as a labelled AVAILABLE SKILLS block and via the
`/skills` slash command.
"""

import os

from core.session import Session, SessionManager
from mu.skills import discover_skills, render_skills_block, Skill, _parse_skill_md
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
        tmp_path, "hello-world", "Say hello.", "Body content here.", trigger="when greeted"
    )
    skill = _parse_skill_md(str(skill_dir / "SKILL.md"))
    assert skill is not None
    assert skill.name == "hello-world"
    assert skill.description == "Say hello."
    assert skill.body == "Body content here."
    assert skill.trigger == "when greeted"


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
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_root = workspace / ".mu" / "skills"
    _write_skill(skills_root, "commit-message", "Local override.", "Body.")
    skills = discover_skills([str(workspace)])
    by_name = {s.name: s for s in skills}
    assert "commit-message" in by_name
    assert by_name["commit-message"].description == "Local override."


def test_render_skills_block_includes_header_and_skill_names():
    s1 = Skill(name="alpha", description="A first skill.", body="A body.", source="/a")
    s2 = Skill(name="beta", description="A second skill.", body="B body.", source="/b")
    block = render_skills_block([s1, s2], budget=10000)
    assert "### AVAILABLE SKILLS" in block
    assert "SKILL: alpha" in block
    assert "SKILL: beta" in block
    assert "A body." in block


def test_render_skills_block_respects_budget():
    huge = "x" * 5000
    skills = [
        Skill(name=f"s{i}", description="d", body=huge, source=f"/s{i}")
        for i in range(5)
    ]
    block = render_skills_block(skills, budget=6000)
    assert len(block) <= 6500  # budget + minor header padding
    # Some skills should be hidden behind a trailer.
    assert "more skill" in block


def test_render_skills_block_empty_when_no_skills():
    assert render_skills_block([], budget=10000) == ""


def test_discover_skills_finds_builtin_commit_message():
    """The bundled `commit-message` skill must be discoverable from the repo."""
    skills = discover_skills([])
    names = {s.name for s in skills}
    assert "commit-message" in names


def test_session_injects_skills_layer_into_system_prompt():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    full = session._inject_hierarchical_context("base prompt")
    # The built-in skills must show up under LAYER 1B.
    assert "LAYER 1B — Installed skills" in full
    assert "SKILL: commit-message" in full


def test_skills_disabled_via_zero_budget():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.variables["skills_max_chars"] = 0
    full = session._inject_hierarchical_context("base prompt")
    assert "LAYER 1B" not in full


def test_skills_slash_command_lists_installed_skills():
    """The /skills command must return a non-empty list with the bundled skills."""
    from mu.commands.skills import skills_cmd

    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    result = skills_cmd(session, "", allow_prompt=False)
    assert result.ok
    skill_names = {s["name"] for s in result.data["skills"]}
    assert "commit-message" in skill_names
    assert "code-review" in skill_names


def test_skills_slash_command_show_one_by_name():
    from mu.commands.skills import skills_cmd

    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    result = skills_cmd(session, "commit-message", allow_prompt=False)
    assert result.ok
    assert result.data["name"] == "commit-message"
    assert os.path.basename(result.data["source"]) == "SKILL.md"
