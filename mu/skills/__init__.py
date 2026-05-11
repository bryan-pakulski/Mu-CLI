"""Skills system — declarative agent extensions discovered from
`SKILL.md` files with YAML frontmatter.

Discovery roots (later wins on name collision):
  1. `<repo>/mu/skills/<name>/SKILL.md`              — built-in
  2. `~/.mu/skills/<name>/SKILL.md`                   — per-user
  3. `<each workspace folder>/.mu/skills/<name>/SKILL.md` — per-project

Frontmatter schema (minimal):

    ---
    name: commit-message
    description: Format a git commit message from staged diff context.
    trigger: optional one-line natural-language hint
    ---

The body below `---` is the skill prompt that gets injected into the
system prompt under an `### AVAILABLE SKILLS` block. Length-capped via
the `skills_max_chars` session variable (default 6144).

This v1 keeps the surface intentionally small:
  * model discovers skills via the system-prompt block and decides when
    to apply them by referring to them by name in its planning text;
  * users list / inspect via `/skills`;
  * no separate `invoke_skill` tool yet — the model can already act on
    any skill by reading its prompt block.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover — yaml is a runtime dependency
    yaml = None  # type: ignore


@dataclass
class Skill:
    name: str
    description: str
    body: str
    source: str  # absolute path to SKILL.md it was loaded from
    trigger: Optional[str] = None
    extra: Dict[str, object] = field(default_factory=dict)


def _builtin_skills_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _user_skills_dir() -> str:
    return os.path.expanduser("~/.mu/skills")


def _parse_skill_md(path: str) -> Optional[Skill]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    if not raw.lstrip().startswith("---"):
        return None
    stripped = raw.lstrip()
    after_first = stripped[3:]
    end_idx = after_first.find("\n---")
    if end_idx == -1:
        return None
    fm_text = after_first[:end_idx]
    body = after_first[end_idx + 4 :].strip("\n").strip()

    meta: Dict[str, object] = {}
    if yaml is not None:
        try:
            loaded = yaml.safe_load(fm_text) or {}
            if isinstance(loaded, dict):
                meta = loaded
        except Exception:
            meta = {}
    if not meta:
        for line in fm_text.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("'").strip('"')

    name = str(meta.get("name") or "").strip()
    if not name:
        # Fall back to the directory name so a skill is still discoverable.
        name = os.path.basename(os.path.dirname(path)) or "skill"
    description = str(meta.get("description") or "").strip()
    trigger_val = meta.get("trigger")
    trigger = str(trigger_val).strip() if trigger_val else None

    extra = {
        k: v for k, v in meta.items() if k not in {"name", "description", "trigger"}
    }
    return Skill(
        name=name,
        description=description,
        body=body,
        source=path,
        trigger=trigger,
        extra=extra,
    )


def _scan_dir(root: str) -> List[Skill]:
    out: List[Skill] = []
    if not root or not os.path.isdir(root):
        return out
    for entry in sorted(os.listdir(root)):
        skill_path = os.path.join(root, entry, "SKILL.md")
        if os.path.isfile(skill_path):
            parsed = _parse_skill_md(skill_path)
            if parsed is not None:
                out.append(parsed)
    return out


def discover_skills(workspace_folders: Optional[List[str]] = None) -> List[Skill]:
    """Discover all installed skills. Later sources override earlier on
    name collision so workspace-level skills can shadow built-ins."""
    skills_by_name: Dict[str, Skill] = {}
    for source in _scan_dir(_builtin_skills_dir()):
        skills_by_name[source.name] = source
    for source in _scan_dir(_user_skills_dir()):
        skills_by_name[source.name] = source
    if workspace_folders:
        for folder in workspace_folders:
            scoped = os.path.join(folder, ".mu", "skills")
            for source in _scan_dir(scoped):
                skills_by_name[source.name] = source
    return sorted(skills_by_name.values(), key=lambda s: s.name.lower())


def render_skills_block(skills: List[Skill], budget: int = 6144) -> str:
    """Render the discovered skills as a system-prompt block.

    Includes name + description + (truncated) body for each. The total
    length is bounded by `budget`; entries past the budget are dropped
    with a "... and N more skills" trailer so the model knows they
    exist.
    """
    if not skills or budget <= 0:
        return ""
    lines: List[str] = ["### AVAILABLE SKILLS"]
    used = len(lines[0]) + 2
    rendered = 0
    for skill in skills:
        header = f"\n#### SKILL: {skill.name}\n{skill.description}".strip()
        block = header + ("\n" + skill.body if skill.body else "")
        if used + len(block) + 2 > budget:
            break
        lines.append(block)
        used += len(block) + 2
        rendered += 1
    remaining = len(skills) - rendered
    if remaining > 0:
        lines.append(f"\n... and {remaining} more skill(s) not shown (budget reached).")
    return "\n".join(lines).strip()


__all__ = ["Skill", "discover_skills", "render_skills_block"]
