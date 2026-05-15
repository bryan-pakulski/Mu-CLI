"""Slash command for browsing bundled documentation.

`/docs` lists every `*.md` under `documentation/` at the repo root.
`/docs <name>` renders one file. Name matching is case-insensitive and
accepts either the bare stem (`skills`, `default_mode`) or the full
filename (`skills.md`).
"""

from __future__ import annotations

import os
from typing import Any, List, Optional, Tuple

from . import CommandResult, command


def _docs_root() -> str:
    """Absolute path to the bundled `documentation/` directory."""
    # mu/commands/docs.py → mu/commands → mu → <repo>/documentation
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", ".."))
    return os.path.join(repo_root, "documentation")


def list_doc_names() -> List[str]:
    """Return the doc filenames (stem without `.md`), sorted, case-insensitive."""
    root = _docs_root()
    if not os.path.isdir(root):
        return []
    out: List[str] = []
    for entry in os.listdir(root):
        path = os.path.join(root, entry)
        if os.path.isfile(path) and entry.lower().endswith(".md"):
            out.append(entry[:-3])
    return sorted(out, key=str.lower)


def _resolve_doc(name: str) -> Optional[Tuple[str, str]]:
    """Resolve a user-supplied name to (stem, absolute_path) or None."""
    needle = name.strip().lower()
    if needle.endswith(".md"):
        needle = needle[:-3]
    for stem in list_doc_names():
        if stem.lower() == needle:
            return stem, os.path.join(_docs_root(), f"{stem}.md")
    return None


def _summarize(path: str) -> str:
    """Return a one-line summary from the first heading or first text line."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    return line.lstrip("# ").strip()
                return line
    except OSError:
        return ""
    return ""


def _emit_info(session: Any, body: str, allow_prompt: bool) -> None:
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "show_info") and allow_prompt:
        ui.show_info(body)


def _render_markdown(session: Any, content: str, allow_prompt: bool) -> None:
    """Render markdown content to the UI console if available."""
    if not allow_prompt:
        return
    ui = getattr(session, "ui", None)
    console = getattr(ui, "console", None) if ui is not None else None
    if console is None:
        _emit_info(session, content, allow_prompt)
        return
    try:
        from rich.markdown import Markdown

        console.print(Markdown(content))
    except Exception:
        # Fall back to plain text if Markdown rendering fails for any reason.
        console.print(content, markup=False)


def _list_docs(session: Any, allow_prompt: bool) -> CommandResult:
    names = list_doc_names()
    if not names:
        msg = "No documentation files found under `documentation/`."
        _emit_info(session, msg, allow_prompt)
        return CommandResult(ok=True, message=msg, data={"docs": []})

    entries = []
    lines = ["Available docs:"]
    for stem in names:
        path = os.path.join(_docs_root(), f"{stem}.md")
        summary = _summarize(path)
        entries.append({"name": stem, "summary": summary, "path": path})
        lines.append(
            f"  • [bold]{stem}[/bold]" + (f" — {summary}" if summary else "")
        )
    lines.append("")
    lines.append("[dim]Show one with[/dim] [bold]/docs <name>[/bold]")
    _emit_info(session, "\n".join(lines), allow_prompt)
    return CommandResult(ok=True, message="\n".join(lines), data={"docs": entries})


def _show_doc(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    resolved = _resolve_doc(name)
    if resolved is None:
        return CommandResult(ok=False, message=f"No doc named {name!r}.")
    stem, path = resolved
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError as exc:
        return CommandResult(ok=False, message=f"Could not read {path}: {exc}")

    _render_markdown(session, content, allow_prompt)
    return CommandResult(
        ok=True,
        message=content,
        data={"name": stem, "path": path},
    )


@command("/docs", help="List bundled docs, or show one: /docs <name>.")
def docs_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    name = (args or "").strip()
    if not name:
        return _list_docs(session, allow_prompt)
    return _show_doc(session, name, allow_prompt)
