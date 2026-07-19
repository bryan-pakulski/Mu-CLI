"""Memory / scratchpad slash command: /memory status|list|clear|save|load."""

import json
import os
import time
from typing import Any, Dict

from utils.helpers import safe_markup

from . import CommandResult, command


def _console(session: Any):
    ui = getattr(session, "ui", None)
    return getattr(ui, "console", None) if ui is not None else None


def _build_stats(store: Any) -> Dict[str, Any]:
    entries = list(store.entries)
    total_hits = sum(int(getattr(e, "hits", 0) or 0) for e in entries)
    top = sorted(
        entries,
        key=lambda e: (int(getattr(e, "hits", 0) or 0), float(getattr(e, "updated_at", 0) or 0)),
        reverse=True,
    )[:3]
    # Count entries by lifecycle status so /memory status shows the
    # active/done/superseded/archived/stale breakdown.
    status_counts: Dict[str, int] = {}
    for e in entries:
        s = getattr(e, "status", "active") or "active"
        status_counts[s] = status_counts.get(s, 0) + 1
    return {
        "entries": len(entries),
        "total_hits": total_hits,
        "avg_hits": (total_hits / len(entries)) if entries else 0.0,
        "top_entries": [e.to_dict() for e in top],
        "status_counts": status_counts,
    }


def _status(session: Any, allow_prompt: bool) -> CommandResult:
    from utils.runtime_metrics import (
        collect_context_layers,
        estimate_active_context_tokens,
    )

    task_stats = _build_stats(session.task_memory)
    scratch_stats = _build_stats(session.turn_scratchpad)
    layer_stats = collect_context_layers(session)
    context_limit = max(
        1, int(session.variables.get("context_token_limit", 256000) or 256000)
    )
    total_tokens = int(estimate_active_context_tokens(session) or 0)
    total_pct = min(100, int(round(100 * total_tokens / context_limit)))

    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                from rich import box
                from rich.table import Table

                table = Table(title="Memory Status", box=box.ROUNDED)
                table.add_column("Type", style="cyan")
                table.add_column("Entries", style="green", justify="right")
                table.add_column("Hits", style="yellow", justify="right")
                table.add_column("Avg Hits", style="magenta", justify="right")
                table.add_column("Description", style="dim")
                table.add_row(
                    "Task Memory",
                    str(task_stats["entries"]),
                    str(task_stats["total_hits"]),
                    f"{task_stats['avg_hits']:.2f}",
                    "Longer-term task context",
                )
                table.add_row(
                    "Scratchpad",
                    str(scratch_stats["entries"]),
                    str(scratch_stats["total_hits"]),
                    f"{scratch_stats['avg_hits']:.2f}",
                    "Short-term turn context",
                )
                console.print(table)

                def print_top(title, stats):
                    from rich.text import Text

                    console.print(f"[bold cyan]{safe_markup(title)} Top Entries[/bold cyan]")
                    if not stats["top_entries"]:
                        console.print("[dim]No entries yet.[/dim]")
                        return
                    top = Table(box=box.SIMPLE)
                    top.add_column("ID", style="dim", justify="right")
                    top.add_column("Hits", style="yellow", justify="right")
                    top.add_column("Tags", style="magenta")
                    top.add_column("Source", style="blue")
                    top.add_column("Preview", style="white")
                    for entry in stats["top_entries"]:
                        tags = ", ".join(entry.get("tags", [])) or "-"
                        preview = str(entry.get("content", "")).replace("\n", " ").strip()
                        if len(preview) > 90:
                            preview = preview[:87] + "..."
                        top.add_row(
                            f"#{entry.get('id')}",
                            str(entry.get("hits", 0)),
                            Text(tags),
                            Text(entry.get("source") or "-"),
                            Text(preview or "(empty)"),
                        )
                    console.print(top)

                print_top("Task Memory", task_stats)
                print_top("Scratchpad", scratch_stats)

                layer_table = Table(title="Hierarchical Context Layers", box=box.SIMPLE)
                layer_table.add_column("Layer", style="cyan")
                layer_table.add_column("Name", style="white")
                layer_table.add_column("Usage", style="yellow", justify="right")
                layer_table.add_column("Fill", style="green", justify="right")
                layer_table.add_column("Description", style="dim")
                from rich.text import Text as _Text

                for layer in layer_stats:
                    cur = int(layer.get("current", 0) or 0)
                    mx = max(1, int(layer.get("maximum", 1) or 1))
                    pct = min(100, int(round((cur / mx) * 100)))
                    layer_table.add_row(
                        _Text(str(layer.get("layer", ""))),
                        _Text(str(layer.get("name", ""))),
                        f"{cur}/{mx}",
                        f"{pct}%",
                        _Text(str(layer.get("description", ""))),
                    )
                # Aggregate row — the global cap (context_token_limit) is
                # what the provider actually enforces, not any single layer.
                total_color = (
                    "red" if total_pct >= 85 else "yellow" if total_pct >= 60 else "green"
                )
                layer_table.add_section()
                layer_table.add_row(
                    "[bold]TOTAL[/bold]",
                    "[bold]All layers (global cap)[/bold]",
                    f"[bold]{total_tokens}/{context_limit}[/bold]",
                    f"[bold {total_color}]{total_pct}%[/bold {total_color}]",
                    "[dim]Sum vs. context_token_limit — what the provider sees.[/dim]",
                )
                console.print(layer_table)
            except Exception:
                pass

    return CommandResult(
        ok=True,
        message=(
            f"Task memory: {task_stats['entries']} entries · "
            f"Scratchpad: {scratch_stats['entries']} entries · "
            f"Context: {total_tokens}/{context_limit} tokens ({total_pct}%)"
        ),
        data={
            "task_memory_count": task_stats["entries"],
            "scratchpad_count": scratch_stats["entries"],
            "task_memory_stats": task_stats,
            "scratchpad_stats": scratch_stats,
            "context_layers": layer_stats,
            "context_total_tokens": total_tokens,
            "context_limit_tokens": context_limit,
            "context_fill_pct": total_pct,
        },
    )


# --------------------------------------------------------------- /memory list

# Canonical list targets — used by both the command and the autocomplete.
# Stores are ones the model writes to via tools; layers are slabs of the
# system prompt the harness assembles each turn.
LIST_TARGETS = (
    "all",
    "task",
    "scratchpad",
    "L0",
    "L1",
    "L1B",
    "L2",
    "L3",
    "L5",
)
_LIST_TARGETS_LOWER = {t.lower(): t for t in LIST_TARGETS}

_LAYER_BUILDERS = {
    "L0": (None, "System prompt"),  # composed via compose_base_system_prompt()
    "L1": ("_build_workspace_context_files", "Workspace files"),
    "L1B": ("_build_skills_block", "Installed skills"),
    "L2": (None, "Conversation summary"),  # straight off session_manager
    "L3": ("_build_active_goal_context", "Active goal"),

    "L5": (None, "Conversation history"),  # off session_manager.history
}


_TOOL_RESULT_PREVIEW = 240


def _render_conversation_history(history: list) -> str:
    """Render the canonical history as a clean conversational view.

    Mirrors what each provider's wire format actually carries — role
    header + content body — without the harness's internal
    `{type, text}` wrappers or JSON indentation. Tool calls / results
    are summarized inline so a long search-result blob doesn't dominate
    the view.
    """
    import json

    if not history:
        return ""
    out: list = []
    for turn in history:
        role = str(turn.get("role", "")).upper()
        out.append(f"━━━ {role} ━━━")
        for part in turn.get("parts", []) or []:
            ptype = part.get("type")
            if ptype == "text":
                out.append(str(part.get("text", "")).strip())
            elif ptype == "tool_call":
                name = part.get("tool_name", "tool")
                args = part.get("tool_args") or {}
                try:
                    arg_str = json.dumps(args, default=str)
                except Exception:
                    arg_str = str(args)
                if len(arg_str) > _TOOL_RESULT_PREVIEW:
                    arg_str = arg_str[:_TOOL_RESULT_PREVIEW] + "…"
                out.append(f"→ {name}({arg_str})")
            elif ptype == "tool_result":
                name = part.get("tool_name", "tool")
                raw = part.get("tool_result")
                if not isinstance(raw, str):
                    try:
                        raw = json.dumps(raw, default=str)
                    except Exception:
                        raw = str(raw)
                preview = raw.replace("\n", " ").strip()
                if len(preview) > _TOOL_RESULT_PREVIEW:
                    preview = preview[:_TOOL_RESULT_PREVIEW] + "…"
                out.append(f"← {name}: {preview}")
            elif ptype == "file":
                fr = part.get("file_ref") or {}
                name = fr.get("display_name") or fr.get("uri") or "file"
                out.append(f"[file: {name}]")
            elif ptype == "image_input":
                img = part.get("image") or {}
                src = img.get("source") or img.get("mime_type", "image")
                out.append(f"[image: {src}]")
            else:
                out.append(f"[{ptype}]")
        out.append("")  # blank line between turns
    return "\n".join(out).rstrip()


def _layer_content(session: Any, layer_id: str) -> str:
    """Return the text body currently injected for one layer.

    For L5 this is a human-readable conversational view, not the raw
    internal `history` dump — the wire format each provider receives
    is leaner than the harness's internal `{role, parts: [...]}`
    representation, so showing the JSON misleads about what the model
    actually sees. Token counts in the panel header are computed
    part-by-part via tiktoken and reflect the real provider cost.
    """
    layer_id = layer_id.upper()
    builder_name, _ = _LAYER_BUILDERS.get(layer_id, (None, None))
    if layer_id == "L0":
        from utils.runtime_metrics import compose_base_system_prompt

        return compose_base_system_prompt(session)
    if layer_id == "L2":
        return str(getattr(session.session_manager, "conversation_summary", "") or "")
    if layer_id == "L5":
        history = list(getattr(session.session_manager, "history", []) or [])
        return _render_conversation_history(history)
    if builder_name is None:
        return ""
    builder = getattr(session, builder_name, None)
    if builder is None:
        return ""
    try:
        return str(builder() or "")
    except Exception:
        return ""


def _status_style(status: str) -> str:
    """Rich style for a memory entry's lifecycle status.

    active=green, done=dim, superseded=yellow, archived=dim+strike, stale=red.
    """
    s = (status or "active").lower()
    if s == "active":
        return "green"
    if s == "done":
        return "dim"
    if s == "superseded":
        return "yellow"
    if s == "archived":
        return "dim strike"
    if s == "stale":
        return "red"
    return "white"


def _print_store_table(console, store, title) -> None:
    from rich import box
    from rich.table import Table

    if not store.entries:
        console.print(f"[dim]No entries in {safe_markup(title)}.[/dim]")
        return
    from rich.text import Text

    table = Table(title=title, box=box.SIMPLE)
    table.add_column("ID", style="dim", justify="right")
    table.add_column("Status", justify="left")
    table.add_column("Hits", style="yellow", justify="right")
    table.add_column("Tags", style="yellow")
    table.add_column("Source", style="blue")
    table.add_column("Content")
    for entry in store.entries:
        tags = ", ".join(entry.tags) if entry.tags else "-"
        status = getattr(entry, "status", "active") or "active"
        table.add_row(
            f"#{entry.id}",
            Text(status, style=_status_style(status)),
            str(entry.hits),
            Text(tags),
            Text(entry.source or "-"),
            Text(entry.content),
        )
    console.print(table)


def _print_layer_panel(console, session, layer_id: str) -> None:
    from rich.panel import Panel
    from rich.text import Text

    from utils.runtime_metrics import collect_context_layers

    body = _layer_content(session, layer_id)
    layer_id = layer_id.upper()
    label = _LAYER_BUILDERS.get(layer_id, (None, layer_id))[1]
    # Pull current/max from the layer table so the panel header carries
    # the token cost.
    cur = mx = 0
    for layer in collect_context_layers(session):
        if str(layer.get("layer", "")).upper() == layer_id:
            cur = int(layer.get("current", 0) or 0)
            mx = int(layer.get("maximum", 1) or 1)
            break
    title = f"{layer_id} — {label} ({cur}/{mx} tokens)"
    if not body:
        console.print(Panel(Text("(empty)", style="dim"), title=title, border_style="dim"))
        return
    console.print(Panel(Text(body), title=title, border_style="cyan"))


def _list(session: Any, target: str, allow_prompt: bool) -> CommandResult:
    target_raw = (target or "all").strip()
    if not target_raw:
        target_raw = "all"
    canonical = _LIST_TARGETS_LOWER.get(target_raw.lower())
    if canonical is None:
        return CommandResult(
            ok=False,
            message=f"Unknown list target {target!r}. Valid: {', '.join(LIST_TARGETS)}",
        )

    def store_entries(store):
        return [e.to_dict() for e in store.entries]

    data: Dict[str, Any] = {"target": canonical}
    console = _console(session) if allow_prompt else None

    if canonical in ("all", "task"):
        data["task_memory"] = store_entries(session.task_memory)
        if console is not None:
            try:
                _print_store_table(console, session.task_memory, "Task Memory")
            except Exception:
                pass
    if canonical in ("all", "scratchpad"):
        data["scratchpad"] = store_entries(session.turn_scratchpad)
        if console is not None:
            try:
                _print_store_table(console, session.turn_scratchpad, "Turn Scratchpad")
            except Exception:
                pass

    if canonical.startswith("L"):
        body = _layer_content(session, canonical)
        data["layer"] = canonical
        data["content"] = body
        if console is not None:
            try:
                _print_layer_panel(console, session, canonical)
            except Exception:
                pass

    return CommandResult(ok=True, message=f"Listed {canonical}.", data=data)


# --------------------------------------------------------------- /memory clear

_CLEAR_TARGETS = {"task", "scratchpad", "all"}


def _clear(session: Any, target: str, allow_prompt: bool) -> CommandResult:
    target = (target or "all").lower()
    if target not in _CLEAR_TARGETS:
        return CommandResult(
            ok=False, message="Usage: /memory clear [task|scratchpad|all]"
        )

    parts = []
    if target in ("all", "task"):
        session.task_memory.clear()
        parts.append("Task memory")
    if target in ("all", "scratchpad"):
        session.turn_scratchpad.clear()
        parts.append("Turn scratchpad")

    msg = " and ".join(parts) + " cleared."
    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                console.print(f"[green]{msg}[/green]")
            except Exception:
                pass
    return CommandResult(ok=True, message=msg)


# --------------------------------------------------------------- /memory save/load

def _memory_dir() -> str:
    from utils.config import HISTORY_DIR
    return os.path.join(HISTORY_DIR, "memory")


def _save_memory(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    name = (name or "").strip()
    if not name:
        return CommandResult(ok=False, message="Usage: /memory save <name>")

    mem_dir = _memory_dir()
    os.makedirs(mem_dir, exist_ok=True)
    filepath = os.path.join(mem_dir, f"{name}.json")

    entries = [e.to_dict() for e in session.task_memory.entries]
    payload = {
        "entries": entries,
        "saved_at": time.time(),
        "session": getattr(session.session_manager, "current_session_name", "") or "",
    }

    try:
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
    except OSError as exc:
        return CommandResult(ok=False, message=f"Failed to save memory: {exc}")

    msg = f"Saved {len(entries)} memory entries to {filepath}"
    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                console.print(f"[green]{msg}[/green]")
            except Exception:
                pass
    return CommandResult(ok=True, message=msg, data={"saved_count": len(entries), "filepath": filepath})


def _load_memory(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    name = (name or "").strip()
    if not name:
        return CommandResult(ok=False, message="Usage: /memory load <name>")

    filepath = os.path.join(_memory_dir(), f"{name}.json")
    if not os.path.isfile(filepath):
        return CommandResult(ok=False, message=f"No saved memory named {name!r} at {filepath}")

    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return CommandResult(ok=False, message=f"Failed to load memory: {exc}")

    entries = payload.get("entries") or []
    loaded = 0
    for entry_dict in entries:
        content = str(entry_dict.get("content", "")).strip()
        if not content:
            continue
        tags = list(entry_dict.get("tags") or [])
        source = str(entry_dict.get("source") or "")
        kind = str(entry_dict.get("kind") or "")
        session.task_memory.save(content, tags=tags, source=source, kind=kind)
        loaded += 1

    msg = f"Loaded {loaded} memory entries from {filepath}"
    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                console.print(f"[green]{msg}[/green]")
            except Exception:
                pass
    return CommandResult(ok=True, message=msg, data={"loaded_count": loaded, "filepath": filepath})


def _list_saved_memory(session: Any, allow_prompt: bool) -> CommandResult:
    mem_dir = _memory_dir()
    if not os.path.isdir(mem_dir):
        msg = "No saved memory files."
        if allow_prompt:
            console = _console(session)
            if console is not None:
                try:
                    console.print(f"[dim]{msg}[/dim]")
                except Exception:
                    pass
        return CommandResult(ok=True, message=msg, data={"saved_files": []})

    import glob
    files = sorted(glob.glob(os.path.join(mem_dir, "*.json")))
    saved = []
    for filepath in files:
        name = os.path.splitext(os.path.basename(filepath))[0]
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            count = len(data.get("entries") or [])
            saved_at = data.get("saved_at") or 0
        except Exception:
            count = 0
            saved_at = 0
        saved.append({"name": name, "entries": count, "saved_at": saved_at, "filepath": filepath})

    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                from rich import box
                from rich.table import Table

                table = Table(title="Saved Memory Files", box=box.SIMPLE)
                table.add_column("Name", style="cyan")
                table.add_column("Entries", style="green", justify="right")
                table.add_column("Saved", style="dim")
                for item in saved:
                    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(item["saved_at"])) if item["saved_at"] else "-"
                    table.add_row(item["name"], str(item["entries"]), ts)
                console.print(table)
            except Exception:
                pass

    return CommandResult(ok=True, message=f"{len(saved)} saved memory file(s).", data={"saved_files": saved})


def _clear_saved_memory(session: Any, allow_prompt: bool) -> CommandResult:
    mem_dir = _memory_dir()
    if not os.path.isdir(mem_dir):
        return CommandResult(ok=True, message="No saved memory files to clear.")

    import glob
    files = glob.glob(os.path.join(mem_dir, "*.json"))
    cleared = 0
    for filepath in files:
        try:
            os.unlink(filepath)
            cleared += 1
        except OSError:
            pass

    msg = f"Cleared {cleared} saved memory file(s)."
    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                console.print(f"[green]{msg}[/green]")
            except Exception:
                pass
    return CommandResult(ok=True, message=msg, data={"cleared_count": cleared})


# --------------------------------------------------------------- dispatch


@command(
    "/memory",
    help=(
        "Inspect the harness state: /memory [status|list <target>|clear <target>]. "
        "List targets: all, task, scratchpad, L1, L1B, L2, L3, L5. "
        "Cross-session: /memory save <name>, /memory load <name>, "
        "/memory list saved, /memory clear saved."
    ),
)
def memory_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    parts = (args or "").split()
    sub = parts[0].lower() if parts else "status"
    rest = parts[1] if len(parts) > 1 else ""

    if sub in ("status", ""):
        return _status(session, allow_prompt)
    if sub == "list":
        if rest.lower() == "saved":
            return _list_saved_memory(session, allow_prompt)
        return _list(session, rest, allow_prompt)
    if sub == "clear":
        if rest.lower() == "saved":
            return _clear_saved_memory(session, allow_prompt)
        return _clear(session, rest, allow_prompt)
    if sub == "save":
        return _save_memory(session, rest, allow_prompt)
    if sub == "load":
        return _load_memory(session, rest, allow_prompt)

    return CommandResult(
        ok=False,
        message=f"Unknown subcommand {sub!r}. Usage: /memory [status|list|clear|save|load]"
    )
