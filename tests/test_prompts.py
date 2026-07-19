"""Tests for the file-based system-prompt library (``mu.prompts``).

Covers the resolution priority ladder (runtime /set > file > hardcoded),
the mtime-keyed cache + reload, template init, critical-anchor drift
detection, and the ``--mode-prompt`` / ``--system-file`` CLI flag helper.

Every test redirects ``prompts_dir()`` to a tmp path so the user's real
``~/.mucli/prompts/`` is never touched.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from mu.prompts import (
    get_base,
    get_mode,
    get_resolved,
    init_templates,
    known_names,
    prompts_dir as _real_prompts_dir,
    reload,
    resolved_snapshot,
    validate,
    write_override,
)


@pytest.fixture
def tmp_prompts_dir(tmp_path, monkeypatch):
    """Redirect the prompt override directory to a tmp path and clear cache."""
    target = tmp_path / "prompts"
    target.mkdir()
    monkeypatch.setattr("mu.prompts.prompts_dir", lambda: str(target))
    reload()
    yield target
    reload()


# ----------------------------------------------------------- resolution


def test_known_names_includes_base_and_all_modes():
    names = known_names()
    assert names[0] == "base"
    # Real agent modes only — history/memory/systemPrompts are GUI view
    # panels, not agent modes, so they are not in known_names().
    for mode in ("default", "debug", "feature", "research", "loop", "security", "teacher"):
        assert mode in names
    for panel in ("history", "memory", "systemPrompts"):
        assert panel not in names


def test_fallback_to_hardcoded_when_no_file(tmp_prompts_dir):
    """No file present → library returns the hardcoded constants verbatim."""
    from utils.config import AGENTIC_MODES, AGENTIC_SYSTEM_BASE

    assert get_base() == AGENTIC_SYSTEM_BASE
    assert get_mode("default") == AGENTIC_MODES["default"]
    assert get_mode("debug") == AGENTIC_MODES["debug"]

    resolved = get_resolved("base")
    assert resolved.source == "hardcoded"
    assert resolved.path is None


def test_file_override_wins_over_hardcoded(tmp_prompts_dir):
    write_override("default", "FILE DEFAULT PROMPT BODY\nmentions spawn_agent and save_memory")
    reload()
    resolved = get_resolved("default")
    assert resolved.source == "file"
    assert resolved.path is not None
    assert "FILE DEFAULT PROMPT BODY" in resolved.text
    assert get_mode("default") == resolved.text


def test_unknown_mode_falls_back_to_default(tmp_prompts_dir):
    assert get_mode("nonexistent-mode") == get_mode("default")


def test_get_resolved_unknown_name_raises(tmp_prompts_dir):
    with pytest.raises(KeyError):
        get_resolved("nope")


# ----------------------------------------------------------- priority ladder


def test_runtime_var_beats_file_beats_hardcoded(tmp_prompts_dir):
    """Priority: session var > file > hardcoded (the loop_body expression)."""
    from utils.config import AGENTIC_SYSTEM_BASE

    # file present
    write_override("base", "FILE BASE")
    reload()
    assert get_base() == "FILE BASE"

    # simulate loop_body: `session.variables.get("agentic_system_base_override") or get_base()`
    session = SimpleNamespace(variables={"agentic_system_base_override": "RUNTIME BASE"})
    effective = session.variables.get("agentic_system_base_override") or get_base()
    assert effective == "RUNTIME BASE"

    # empty-string override falls through to file
    session2 = SimpleNamespace(variables={"agentic_system_base_override": ""})
    effective2 = session2.variables.get("agentic_system_base_override") or get_base()
    assert effective2 == "FILE BASE"

    # no file, no var → hardcoded
    os.remove(os.path.join(str(tmp_prompts_dir), "base.md"))
    reload()
    session3 = SimpleNamespace(variables={})
    effective3 = session3.variables.get("agentic_system_base_override") or get_base()
    assert effective3 == AGENTIC_SYSTEM_BASE


def test_resolved_snapshot_layers_runtime_override(tmp_prompts_dir):
    write_override("default", "FILE DEFAULT")
    reload()
    session = SimpleNamespace(variables={"agentic_mode_prompt_default": "RUNTIME"})
    snap = resolved_snapshot(session)
    assert snap["default"]["source"] == "override"
    assert snap["default"]["has_override"] is True
    assert snap["default"]["chars"] == len("RUNTIME")
    # base has no override and no file → hardcoded
    assert snap["base"]["source"] == "hardcoded"


# ----------------------------------------------------------- cache + reload


def test_reload_picks_up_mtime_change(tmp_prompts_dir):
    write_override("debug", "VERSION ONE")
    reload()
    assert get_mode("debug") == "VERSION ONE"
    # rewrite without going through the cache-aware write path
    path = os.path.join(str(tmp_prompts_dir), "debug.md")
    # bump mtime deterministically
    import time

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("---\nname: debug\nversion: 2\n---\nVERSION TWO\n")
    os.utime(path, (time.time() + 5, time.time() + 5))
    reload()
    assert get_mode("debug") == "VERSION TWO"


# ----------------------------------------------------------- init / templates


def test_init_templates_writes_refined_base_and_default(tmp_prompts_dir):
    written = init_templates(["base", "default"])
    assert set(written.keys()) == {"base", "default"}
    for name in ("base", "default"):
        assert os.path.isfile(written[name])
    # The bundled templates are the refined versions (frontmatter version: 2).
    base_resolved = get_resolved("base")
    assert base_resolved.source == "file"
    assert base_resolved.version == 2


def test_init_templates_seeds_other_modes_from_hardcoded(tmp_prompts_dir):
    from utils.config import AGENTIC_MODES

    written = init_templates(["debug"])
    assert "debug" in written
    # debug has no bundled refined template → seeded verbatim from hardcoded
    assert get_mode("debug") == AGENTIC_MODES["debug"]


def test_init_templates_skips_existing_without_force(tmp_prompts_dir):
    init_templates(["base"])
    again = init_templates(["base"])
    assert again == {}


def test_init_templates_force_overwrites(tmp_prompts_dir):
    init_templates(["base"])
    again = init_templates(["base"], force=True)
    assert "base" in again


# ----------------------------------------------------------- validation


def test_validate_detects_missing_anchors():
    missing = validate("base", "just some text with bash but nothing else")
    assert "read_file" in missing
    assert "apply_diff" in missing
    assert "plan mode" in missing


def test_validate_passes_for_hardcoded_constants():
    """The shipped hardcoded prompts must validate clean (no drift)."""
    from utils.config import AGENTIC_MODES, AGENTIC_SYSTEM_BASE

    assert validate("base", AGENTIC_SYSTEM_BASE) == []
    for mode in ("default", "debug", "feature", "research", "loop", "security"):
        assert validate(mode, AGENTIC_MODES[mode]) == [], f"mode {mode} drifted"


def test_validate_passes_for_refined_templates(tmp_prompts_dir):
    init_templates(["base", "default"])
    reload()
    assert validate("base", get_resolved("base").text) == []
    assert validate("default", get_resolved("default").text) == []


def test_validate_anyof_tokens():
    # "parallel" OR "concurrent" — having one satisfies the check.
    text = "bash read_file apply_diff search_for_string retrieve_relevant_context spawn_agent todo_write save_memory save_scratchpad flush plan mode parallel"
    assert validate("base", text) == []
    text2 = text.replace("parallel", "concurrent")
    assert validate("base", text2) == []


# ----------------------------------------------------------- refined length


def test_refined_base_no_longer_than_original():
    from utils.config import AGENTIC_SYSTEM_BASE

    refined = get_resolved("base").text  # hardcoded fallback == original here
    # Read the refined template directly from the package.
    tmpl = os.path.join(os.path.dirname(_real_prompts_dir.__module__) or ".", "templates", "base.md")
    # Fall back to the known templates dir via the module location.
    import mu.prompts as _p

    tmpl = os.path.join(os.path.dirname(_p.__file__), "templates", "base.md")
    with open(tmpl, encoding="utf-8") as fh:
        from mu.prompts import _split_frontmatter

        _, body = _split_frontmatter(fh.read())
    assert len(body) <= len(AGENTIC_SYSTEM_BASE)
    assert validate("base", body) == []


def test_refined_default_no_longer_than_original():
    from utils.config import AGENTIC_MODES

    import mu.prompts as _p
    from mu.prompts import _split_frontmatter

    tmpl = os.path.join(os.path.dirname(_p.__file__), "templates", "default.md")
    with open(tmpl, encoding="utf-8") as fh:
        _, body = _split_frontmatter(fh.read())
    assert len(body) <= len(AGENTIC_MODES["default"])
    assert validate("default", body) == []


# ----------------------------------------------------------- CLI flag helper


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_apply_prompt_flags_mode_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("mu.prompts.prompts_dir", lambda: str(tmp_path / "p"))
    reload()
    f = tmp_path / "base.md"
    f.write_text("MY BASE PROMPT\nwith spawn_agent\n")
    args = SimpleNamespace(system_file=None, mode_prompt=[f"base={f}"])
    session = SimpleNamespace(
        system_instruction="orig",
        variables={},
    )
    from mu.commands._prompt_flags import apply_prompt_flags

    apply_prompt_flags(session, args)
    assert session.variables["agentic_system_base_override"] == "MY BASE PROMPT\nwith spawn_agent\n"


def test_apply_prompt_flags_mode_prompt_for_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("mu.prompts.prompts_dir", lambda: str(tmp_path / "p"))
    reload()
    f = tmp_path / "default.md"
    f.write_text("MODE PROMPT")
    args = SimpleNamespace(system_file=None, mode_prompt=["default=" + str(f)])
    session = SimpleNamespace(system_instruction="orig", variables={})
    from mu.commands._prompt_flags import apply_prompt_flags

    apply_prompt_flags(session, args)
    assert session.variables["agentic_mode_prompt_default"] == "MODE PROMPT"


def test_apply_prompt_flags_system_file(tmp_path):
    f = tmp_path / "sys.txt"
    f.write_text("SYSTEM INSTRUCTION FROM FILE")
    args = SimpleNamespace(system_file=str(f), mode_prompt=None)
    session = SimpleNamespace(system_instruction="orig", variables={})
    from mu.commands._prompt_flags import apply_prompt_flags

    apply_prompt_flags(session, args)
    assert session.system_instruction == "SYSTEM INSTRUCTION FROM FILE"


def test_apply_prompt_flags_unknown_name_raises(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("x")
    args = SimpleNamespace(system_file=None, mode_prompt=[f"bogus={f}"])
    session = SimpleNamespace(system_instruction="orig", variables={})
    from mu.commands._prompt_flags import apply_prompt_flags

    with pytest.raises(SystemExit):
        apply_prompt_flags(session, args)