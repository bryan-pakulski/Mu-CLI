"""File-based system-prompt library.

Lets users override the base agentic prompt and per-mode workflow prompts
from files on disk without editing Python source. Resolution priority
(highest first):

  1. **runtime session-variable override** set via ``/set`` —
     ``agentic_system_base_override`` or ``agentic_mode_prompt_<mode>``.
  2. **bundled template** under ``mu/prompts/templates/``.
  3. **hardcoded fallback** in ``utils/config.py`` —
     ``AGENTIC_SYSTEM_BASE`` / ``AGENTIC_MODES``.

Layers 2 and 3 are this library's responsibility; layer 1 is handled by
the call site in ``mu/agent/loop_body.py`` via ``session.variables.get(
key) or PromptLibrary.get_*()`` so a runtime ``/set`` always wins.

File format (YAML frontmatter, optional but recommended):

    ---
    name: default
    version: 2
    description: Collation-aware default coding workflow.
    ---
    <prompt body — markdown, exactly what the model sees>

The loader caches resolved prompts by file mtime. ``reload()`` clears the
cache so ``/prompts reload`` (or the next cache miss) picks up edits
without a restart. No external dependencies — frontmatter parsing reuses
the same YAML already required by ``mu.skills``, with a manual fallback
when PyYAML is absent.

``validate()`` checks a resolved prompt still names the critical tool
surface / workflow anchors the harness depends on (the same substrings
pinned by ``tests/test_mode_sota_patterns.py``), so a hand-edited file
that silently drops ``spawn_agent`` or ``plan mode`` is surfaced as a
warning rather than a silent regression.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from utils.config import (
    AGENTIC_MODES,
    AGENTIC_SYSTEM_BASE,
    HISTORY_DIR,
    NUDGE_EMPTY_RESPONSE,
)

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover — yaml is a runtime dependency
    yaml = None  # type: ignore


logger = logging.getLogger(__name__)


# --- locations -----------------------------------------------------------

def prompts_dir() -> str:
    """Directory holding file-based prompt overrides (``$MUCLI_HOME/prompts``)."""
    return os.path.join(os.path.expanduser(str(HISTORY_DIR)), "prompts")


def _builtin_templates_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def known_names() -> List[str]:
    """``["base", <mode>, ...]`` — base plus every registered mode."""
    return ["base", *sorted(AGENTIC_MODES.keys())]


def _file_path(name: str) -> str:
    return os.path.join(prompts_dir(), f"{name}.md")


# --- frontmatter parsing -------------------------------------------------

def _split_frontmatter(raw: str) -> Tuple[Dict[str, Any], str]:
    """Return (frontmatter, body). Empty dict when no frontmatter present."""
    if not raw.lstrip().startswith("---"):
        return {}, raw
    stripped = raw.lstrip()
    after = stripped[3:]
    end = after.find("\n---")
    if end == -1:
        return {}, raw
    fm_text = after[:end]
    body = after[end + 4:].strip("\n").strip()
    meta: Dict[str, Any] = {}
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
    return meta, body


def _coerce_version(meta: Dict[str, Any]) -> Optional[int]:
    v = meta.get("version")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# --- resolved prompt -----------------------------------------------------

@dataclass
class ResolvedPrompt:
    name: str
    text: str
    source: str  # "file" | "hardcoded"
    path: Optional[str]
    version: Optional[int]

    @property
    def chars(self) -> int:
        return len(self.text)


# mtime-keyed cache: name -> (mtime, ResolvedPrompt)
_CACHE: Dict[str, Tuple[float, ResolvedPrompt]] = {}


def reload() -> None:
    """Drop the cache so the next read re-stats disk."""
    _CACHE.clear()


def _resolve_file(name: str) -> Optional[ResolvedPrompt]:
    path = _file_path(name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _CACHE.get(name)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:
        logger.warning("prompt %s: read failed: %s", name, exc)
        return None
    meta, body = _split_frontmatter(raw)
    resolved = ResolvedPrompt(
        name=name,
        text=body,
        source="file",
        path=path,
        version=_coerce_version(meta),
    )
    _CACHE[name] = (mtime, resolved)
    return resolved


def _resolve_builtin(name: str) -> Optional[ResolvedPrompt]:
    """Resolve repository-owned prompt templates before legacy fallback."""
    path = os.path.join(_builtin_templates_dir(), f"{name}.md")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    meta, body = _split_frontmatter(raw)
    return ResolvedPrompt(name=name, text=body, source="builtin", path=path,
                          version=_coerce_version(meta))


def _hardcoded(name: str) -> ResolvedPrompt:
    if name == "base":
        text = AGENTIC_SYSTEM_BASE
    elif name in AGENTIC_MODES:
        text = AGENTIC_MODES[name]
    else:
        raise KeyError(f"unknown prompt name: {name!r}")
    return ResolvedPrompt(name=name, text=text, source="hardcoded", path=None, version=1)


def get_base() -> str:
    """Resolved base prompt text (file > hardcoded). Layer 1 (runtime var)
    is applied by the call site, not here."""
    return (_resolve_file("base") or _resolve_builtin("base") or _hardcoded("base")).text


def get_mode(mode: str) -> str:
    """Resolved per-mode prompt text (file > hardcoded). Falls back to
    ``default`` when ``mode`` is unknown."""
    if mode not in AGENTIC_MODES:
        mode = "default"
    return (_resolve_file(mode) or _resolve_builtin(mode) or _hardcoded(mode)).text


def get_resolved(name: str) -> ResolvedPrompt:
    """Full resolution (with meta) — user file > bundled > fallback."""
    if name not in known_names():
        raise KeyError(f"unknown prompt name: {name!r}")
    return _resolve_file(name) or _resolve_builtin(name) or _hardcoded(name)


def resolved_snapshot(session: Any = None) -> Dict[str, Dict[str, Any]]:
    """Per-prompt resolution summary for ``/prompts`` and the GUI.

    When ``session`` is supplied, the runtime session-variable override
    (layer 1) is layered on top so the display shows all three tiers and
    which one is currently effective.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name in known_names():
        var_key = (
            "agentic_system_base_override"
            if name == "base"
            else f"agentic_mode_prompt_{name}"
        )
        runtime = None
        if session is not None:
            val = session.variables.get(var_key)
            if isinstance(val, str) and val.strip():
                runtime = val
        resolved = get_resolved(name)
        if runtime is not None:
            effective_text = runtime
            effective_source = "override"
            effective_path = None
        else:
            effective_text = resolved.text
            effective_source = resolved.source
            effective_path = resolved.path
        out[name] = {
            "source": effective_source,
            "path": effective_path,
            "version": resolved.version if runtime is None else None,
            "chars": len(effective_text),
            "has_override": runtime is not None,
        }
    return out


# --- critical-section validation ----------------------------------------

# Substrings each prompt must still name. A tuple means any-of. Mirrors
# the assertions in tests/test_mode_sota_patterns.py so a hand-edited file
# that drops a frontier feature is caught as drift, not a silent regression.
_CRITICAL: Dict[str, List[Union[str, Tuple[str, ...]]]] = {
    "base": [
        "bash", "read_file", "apply_diff", "search_for_string",
        "retrieve_relevant_context", "spawn_agent", "todo_write",
        "save_memory", "save_scratchpad", "flush", "plan mode",
        ("parallel", "concurrent"),
    ],
    "default": [
        "search_memory", "retrieve_relevant_context", "bash", "verif",
        "todo_write", ("parallel", "concurrent"), "spawn_agent", "save_memory",
    ],
    "debug": [
        "search_memory", "reproduce", "locate", "verify", "parallel",
        "bisect", "save_memory",
        ("whole test", "wider", "full test", "race"),
    ],
    "feature": [
        "create_feature_task", "get_current_task", "get_tasks",
        "update_task_status", "approve_feature_task", "parallel",
        "spawn_agent", "save_memory", "save_scratchpad", "raise_blocker",
    ],
    "research": [
        "search_memory", "retrieve_relevant_context", "parallel",
        "spawn_agent", "citation", "credibility", "[^n]", "save_memory",
        "WORKFLOW",
    ],
    "loop": [
        "todo_write", "todo_set_status", "parallel",
        "retrieve_relevant_context", "spawn_agent",
        ("evidence", "verify"), "raise_blocker", "memory",
        ("save_memory", "save_scratchpad"),
    ],
    "security": [
        "create_security_report", "add_security_finding",
        "verify_security_proof", "approve_security_finding",
    ],
    "history": [],
    "teacher": [],
}


def _has(text_lower: str, token: Union[str, Tuple[str, ...]]) -> bool:
    if isinstance(token, tuple):
        return any(t.lower() in text_lower for t in token)
    return token.lower() in text_lower


def validate(name: str, text: str) -> List[str]:
    """Return a list of warning strings for missing critical anchors.

    Empty list means the prompt still names every required tool / workflow
    anchor for its kind. ``history`` and ``teacher`` have no pinned anchors
    (their content is free-form) and always validate clean.
    """
    anchors = _CRITICAL.get(name, [])
    lower = text.lower()
    missing: List[str] = []
    for token in anchors:
        if _has(lower, token):
            continue
        label = "|".join(token) if isinstance(token, tuple) else token
        missing.append(label)
    return missing


# --- init / write / read -------------------------------------------------

def _template_source(name: str) -> str:
    """Return the text to seed a new file with for ``name``.

    Bundled refined templates (``templates/base.md``, ``templates/default.md``)
    are used when present; every other name is seeded from its hardcoded
    fallback so ``/prompts init <mode>`` externalizes the current prompt
    verbatim for the user to edit.
    """
    bundled = os.path.join(_builtin_templates_dir(), f"{name}.md")
    if os.path.isfile(bundled):
        try:
            with open(bundled, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            pass
    # Seed from the hardcoded constant with a minimal frontmatter header.
    if name == "base":
        body = AGENTIC_SYSTEM_BASE
        meta = {"name": "base", "version": 1, "description": "Autonomous AI Software Engineer base prompt."}
    elif name in AGENTIC_MODES:
        body = AGENTIC_MODES[name]
        meta = {"name": name, "version": 1, "description": f"{name} mode workflow."}
    else:
        raise KeyError(f"unknown prompt name: {name!r}")
    return _render_with_frontmatter(meta, body)


def _render_with_frontmatter(meta: Dict[str, Any], body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip("\n"))
    return "\n".join(lines) + "\n"


def init_templates(
    names: Optional[List[str]] = None, *, force: bool = False
) -> Dict[str, str]:
    """Write template files into ``$MUCLI_HOME/prompts/`` so the user can
    edit them. Returns ``{name: path}`` of files written. Skips names that
    already exist unless ``force``."""
    if names is None:
        names = known_names()
    written: Dict[str, str] = {}
    os.makedirs(prompts_dir(), exist_ok=True)
    for name in names:
        if name not in known_names():
            continue
        path = _file_path(name)
        if os.path.exists(path) and not force:
            continue
        content = _template_source(name)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            logger.warning("prompts init %s: write failed: %s", name, exc)
            continue
        written[name] = path
    reload()
    return written


def write_override(name: str, text: str, *, version: Optional[int] = None) -> str:
    """Persist ``text`` as the file override for ``name`` (GUI editor PUT)."""
    if name not in known_names():
        raise KeyError(f"unknown prompt name: {name!r}")
    os.makedirs(prompts_dir(), exist_ok=True)
    path = _file_path(name)
    meta = {"name": name, "version": version if version is not None else 1}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_render_with_frontmatter(meta, text))
    reload()
    return path


def read_override_raw(name: str) -> Optional[str]:
    """Return the raw file content (frontmatter + body) for ``name``, or
    ``None`` when no file override exists."""
    path = _file_path(name)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def nudge_empty_response() -> str:
    """Accessor kept here so the loop has one import for all prompt text."""
    return NUDGE_EMPTY_RESPONSE


__all__ = [
    "ResolvedPrompt",
    "get_base",
    "get_mode",
    "get_resolved",
    "init_templates",
    "known_names",
    "nudge_empty_response",
    "prompts_dir",
    "read_override_raw",
    "reload",
    "resolved_snapshot",
    "validate",
    "write_override",
]
