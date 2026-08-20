"""Memory / scratchpad slash command: /memory status|list|clear|save|load."""

import json
import os
import shlex
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
    try:
        durable_stats = session.get_durable_memory_service().stats_for_session(session)
    except Exception:
        durable_stats = {"total": 0, "pinned": 0, "by_lifecycle": {}, "scopes": []}
    layer_stats = collect_context_layers(session)
    # The raw provider window vs. the compactor's *effective* ceiling.
    # The compactor fires on `drift_corrected_context_limit` — the raw
    # `context_token_limit` divided by the provider's safety factor (2.5 for
    # Ollama) and any learned cl100k→real drift — NOT on the raw window. The
    # old fill% compared against the raw window, so /memory could read "60%
    # full" while emergency compaction was already firing. Use the effective
    # ceiling so the displayed fill matches what the compactor actually
    # enforces. (For providers with no safety factor — OpenAI/Gemini — the
    # effective ceiling equals the raw window, so this is a no-op there.)
    from mu.session.budgets import (
        drift_corrected_context_limit,
        effective_drift_ratio,
    )

    raw_limit = max(
        1, int(session.variables.get("context_token_limit", 256000) or 256000)
    )
    try:
        effective_limit = max(1, int(drift_corrected_context_limit(session)))
    except Exception:  # noqa: BLE001
        effective_limit = raw_limit
    try:
        safety_factor = float(effective_drift_ratio(session))
    except Exception:  # noqa: BLE001
        safety_factor = 1.0
    context_limit = effective_limit
    total_tokens = int(estimate_active_context_tokens(session) or 0)
    total_pct = min(999, int(round(100 * total_tokens / max(1, effective_limit))))

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
                table.add_row(
                    "Memory Ledger",
                    str(durable_stats.get("total", 0)),
                    "-",
                    "-",
                    (
                        f"Cross-session scoped knowledge · "
                        f"{durable_stats.get('pinned', 0)} pinned"
                    ),
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
                # Aggregate row — the compactor's effective ceiling
                # (drift_corrected_context_limit), not the raw window. The
                # layer sums are cl100k estimates; the effective ceiling is
                # the raw window ÷ the safety/drift factor, so the ratio is
                # the true real-token fill even though numerator and
                # denominator are in different absolute units.
                total_color = (
                    "red" if total_pct >= 85 else "yellow" if total_pct >= 60 else "green"
                )
                cap_label = (
                    f"All layers vs. effective cap (÷{safety_factor:.1f})"
                    if safety_factor > 1.01
                    else "All layers (global cap)"
                )
                cap_desc = (
                    f"Sum vs. effective limit ({effective_limit:,} = "
                    f"raw {raw_limit:,} ÷ {safety_factor:.1f})."
                    if safety_factor > 1.01
                    else "Sum vs. context_token_limit — what the provider sees."
                )
                layer_table.add_section()
                layer_table.add_row(
                    "[bold]TOTAL[/bold]",
                    f"[bold]{cap_label}[/bold]",
                    f"[bold]{total_tokens}/{context_limit}[/bold]",
                    f"[bold {total_color}]{total_pct}%[/bold {total_color}]",
                    f"[dim]{cap_desc}[/dim]",
                )
                console.print(layer_table)

                # --- Efficiency metrics (spec #12) ---
                try:
                    from mu.session.efficiency_metrics import (
                        collect_efficiency_metrics,
                    )

                    eff = collect_efficiency_metrics(session)
                    eff_table = Table(
                        title="Tool-Output Efficiency (this turn)",
                        box=box.SIMPLE,
                    )
                    eff_table.add_column("Metric", style="cyan")
                    eff_table.add_column("Value", style="green", justify="right")
                    eff_table.add_row(
                        "Raw tool-output tokens",
                        f"{eff.get('raw_tool_tokens', 0):,}",
                    )
                    eff_table.add_row(
                        "Injected tool tokens",
                        f"{eff.get('injected_tool_tokens', 0):,}",
                    )
                    eff_table.add_row(
                        "Tokens saved (observed)",
                        f"{eff.get('tokens_saved', 0):,}",
                    )
                    eff_table.add_row(
                        "Compression ratio",
                        f"{eff.get('compression_ratio', 0.0):.1%}",
                    )
                    eff_table.add_row(
                        "Omitted (stored-ref) results",
                        str(eff.get("omitted_results", 0)),
                    )
                    cache = eff.get("cache", {}) or {}
                    eff_table.add_section()
                    eff_table.add_row(
                        "Cache locator hits",
                        str(cache.get("locator_hits", 0)),
                    )
                    eff_table.add_row(
                        "Cache disk fallback hits",
                        str(cache.get("disk_hits", 0)),
                    )
                    eff_table.add_row(
                        "Cache invalidations",
                        str(cache.get("invalidations", 0)),
                    )
                    eff_table.add_row(
                        "Cache evictions",
                        str(cache.get("evictions", 0)),
                    )
                    eff_table.add_row(
                        "Dup-read bytes avoided",
                        f"{cache.get('dup_bytes_avoided', 0):,}",
                    )
                    eff_table.add_row(
                        "Retrieval calls (recall/result_*)",
                        str(eff.get("retrieval_calls", 0)),
                    )
                    if eff.get("tool_output_share"):
                        eff_table.add_row(
                            "Tool-output share of context",
                            f"{eff.get('tool_output_share', 0.0):.1%}",
                        )
                    console.print(eff_table)
                except Exception:
                    pass
            except Exception:
                pass

    return CommandResult(
        ok=True,
        message=(
            f"Task memory: {task_stats['entries']} entries · "
            f"Ledger: {durable_stats.get('total', 0)} entries · "
            f"Scratchpad: {scratch_stats['entries']} entries · "
            f"Context: {total_tokens}/{context_limit} tokens ({total_pct}%)"
        ),
        data={
            "task_memory_count": task_stats["entries"],
            "scratchpad_count": scratch_stats["entries"],
            "task_memory_stats": task_stats,
            "scratchpad_stats": scratch_stats,
            "durable_memory_stats": durable_stats,
            "context_layers": layer_stats,
            "context_total_tokens": total_tokens,
            "context_limit_tokens": context_limit,
            "context_limit_tokens_raw": raw_limit,
            "context_safety_factor": round(safety_factor, 3),
            "context_fill_pct": total_pct,
        },
    )


def _durable_service(session: Any):
    return session.get_durable_memory_service()


def _print_durable_table(console, items, title: str = "Cross-session Memory Ledger") -> None:
    if console is None:
        return
    from rich import box
    from rich.table import Table
    from rich.text import Text

    table = Table(title=title, box=box.SIMPLE)
    table.add_column("ID", style="dim")
    table.add_column("Scope", style="cyan")
    table.add_column("Kind", style="magenta")
    table.add_column("State", style="yellow")
    table.add_column("Use", justify="right")
    table.add_column("Statement")
    for item in items:
        table.add_row(
            item.id.split("-")[0],
            item.scope_type,
            item.kind,
            ("📌 " if item.pinned else "") + item.lifecycle,
            str(item.recall_count),
            Text(item.statement or "(content forgotten)"),
        )
    if not items:
        console.print("[dim]No durable memories matched.[/dim]")
    else:
        console.print(table)


def _parse_remember_args(raw: str) -> tuple[str, Dict[str, Any]]:
    tokens = shlex.split(raw or "")
    options: Dict[str, Any] = {
        "scope": "auto",
        "kind": "observation",
        "pinned": False,
        "egress_policy": "any",
        "tags": [],
    }
    text_parts = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--pin":
            options["pinned"] = True
            index += 1
            continue
        if token in {"--scope", "--kind", "--egress", "--tags"} and index + 1 < len(tokens):
            value = tokens[index + 1]
            if token == "--scope":
                options["scope"] = value
            elif token == "--kind":
                options["kind"] = value
            elif token == "--egress":
                options["egress_policy"] = value
            else:
                options["tags"] = [part.strip() for part in value.split(",") if part.strip()]
            index += 2
            continue
        text_parts.append(token)
        index += 1
    return " ".join(text_parts).strip(), options


@command(
    "/remember",
    help=(
        "Store an explicit durable memory without an approval prompt: "
        "/remember <text> [--scope repo] [--kind decision] [--pin]."
    ),
)
def remember_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    try:
        statement, options = _parse_remember_args(args)
    except ValueError as exc:
        return CommandResult(ok=False, message=f"Invalid /remember arguments: {exc}")
    if not statement:
        return CommandResult(
            ok=False,
            message=(
                "Usage: /remember <text> [--scope personal|workspace|repository|"
                "branch|feature] [--kind decision|finding|preference|procedure] [--pin]"
            ),
        )
    try:
        item, created = _durable_service(session).remember(
            session,
            statement,
            kind=options["kind"],
            scope=options["scope"],
            tags=options["tags"],
            actor="user",
            trust_origin="user_explicit",
            verification="user_confirmed",
            confidence=1.0,
            pinned=options["pinned"],
            egress_policy=options["egress_policy"],
            reason="explicit /remember command",
        )
    except Exception as exc:
        return CommandResult(ok=False, message=f"Memory not stored: {exc}")
    verb = "Stored" if created else "Reinforced"
    message = (
        f"{verb} durable memory {item.id} "
        f"[{item.scope_type}/{item.kind}] · visible in Memory Center."
    )
    console = _console(session) if allow_prompt else None
    if console is not None:
        console.print(f"[green]{safe_markup(message)}[/green]")
    return CommandResult(ok=True, message=message, data={"memory": item.to_dict()})


def _durable_list(
    session: Any, query: str, allow_prompt: bool, *, lifecycle: str | None = None
) -> CommandResult:
    try:
        items = _durable_service(session).list_for_session(
            session, query=query, lifecycle=lifecycle, limit=200
        )
    except Exception as exc:
        return CommandResult(ok=False, message=f"Memory ledger unavailable: {exc}")
    _print_durable_table(_console(session) if allow_prompt else None, items)
    return CommandResult(
        ok=True,
        message=f"{len(items)} durable memories",
        data={"memories": [item.to_dict() for item in items]},
    )


def _durable_show(session: Any, memory_id: str, allow_prompt: bool) -> CommandResult:
    try:
        item = _durable_service(session).get_for_session(session, memory_id)
    except Exception as exc:
        return CommandResult(ok=False, message=f"Memory lookup failed: {exc}")
    if item is None:
        return CommandResult(ok=False, message=f"Durable memory {memory_id!r} not found.")
    memory_id = item.id
    data = item.to_dict()
    data["events"] = _durable_service(session).ledger.events(
        memory_id=memory_id, limit=50
    )
    data["revisions"] = _durable_service(session).ledger.revisions(
        memory_id, limit=50
    )
    if allow_prompt and _console(session) is not None:
        _print_durable_table(_console(session), [item], title="Memory detail")
        _console(session).print_json(data=data)
    return CommandResult(ok=True, message=item.statement, data=data)


def _durable_why(session: Any, receipt_id: str, allow_prompt: bool) -> CommandResult:
    session_name = str(session.session_manager.current_session_name or "")
    receipt = _durable_service(session).ledger.get_recall(
        receipt_id, session_name=session_name
    )
    if receipt is None:
        return CommandResult(ok=False, message="No recall receipt is available yet.")
    if allow_prompt and _console(session) is not None:
        console = _console(session)
        console.print(
            f"[bold cyan]Recall {receipt['id']}[/bold cyan] · "
            f"{len(receipt['included'])} included · {receipt['token_count']} tokens"
        )
        for candidate in receipt["included"]:
            memory = candidate.get("memory", {})
            reasons = candidate.get("reasons", {})
            console.print(
                f"[green]used[/green] {str(memory.get('id', ''))[:8]} "
                f"[{memory.get('scope', {}).get('type', '')}] "
                f"score={candidate.get('score')} · {memory.get('statement', '')}"
            )
            console.print(f"  [dim]{safe_markup(json.dumps(reasons, sort_keys=True))}[/dim]")
        for candidate in receipt["excluded"][:10]:
            memory = candidate.get("memory", {})
            console.print(
                f"[dim]skip {str(memory.get('id', ''))[:8]} · "
                f"{candidate.get('exclusion_reason', '')} · "
                f"{memory.get('statement', '')}[/dim]"
            )
    return CommandResult(ok=True, message=f"Recall receipt {receipt['id']}", data=receipt)


def _durable_action(
    session: Any, action: str, memory_id: str, allow_prompt: bool
) -> CommandResult:
    if not memory_id:
        return CommandResult(ok=False, message=f"Usage: /memory {action} <uuid>")
    try:
        resolved = _durable_service(session).get_for_session(session, memory_id)
        if resolved is None:
            return CommandResult(
                ok=False, message=f"Durable memory {memory_id!r} not found."
            )
        memory_id = resolved.id
        item = _durable_service(session).ledger.action(
            memory_id,
            action,
            actor="user",
            reason=f"TUI /memory {action}",
        )
    except Exception as exc:
        return CommandResult(ok=False, message=f"Memory action failed: {exc}")
    message = f"Memory {memory_id} → {item.lifecycle}"
    if allow_prompt and _console(session) is not None:
        _console(session).print(f"[green]{safe_markup(message)}[/green]")
    return CommandResult(ok=True, message=message, data={"memory": item.to_dict()})


# --------------------------------------------------------------- /memory list

# Canonical list targets — used by both the command and the autocomplete.
# Stores are ones the model writes to via tools; layers are slabs of the
# system prompt the harness assembles each turn.
LIST_TARGETS = (
    "L1A",
    "all",
    "task",
    "scratchpad",
    "L0",
    "L1B",
    "L2",
    "L3",
    "L5",
)
_LIST_TARGETS_LOWER = {t.lower(): t for t in LIST_TARGETS}

_LAYER_BUILDERS = {
    "L1A": ("_build_context_files_block", "Context files"),
    "L0": (None, "System prompt"),  # composed via compose_base_system_prompt()
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
                out.append(f"→ tool call - name: {name}, args: {arg_str}")
            elif ptype == "tool_result":
                name = part.get("tool_name", "tool")
                cache_key = part.get("cache_key")
                raw = part.get("tool_result")
                # Structured envelope dict — extract ok/summary for compact ref
                if isinstance(raw, dict):
                    ok = raw.get("ok")
                    result_state = "success" if ok else "error"
                    summary = str(raw.get("summary") or "").strip().replace("\n", " ")
                    if len(summary) > _TOOL_RESULT_PREVIEW:
                        summary = summary[:_TOOL_RESULT_PREVIEW] + "…"
                else:
                    ok = not str(raw or "").startswith("Error")
                    result_state = "success" if ok else "error"
                    summary = str(raw or "").replace("\n", " ").strip()
                    if len(summary) > _TOOL_RESULT_PREVIEW:
                        summary = summary[:_TOOL_RESULT_PREVIEW] + "…"
                if cache_key:
                    out.append(
                        f"← tool call - name: {name}, result: {result_state}, ref: {cache_key}"
                    )
                else:
                    # No cache_key — include short preview since no ref to recall
                    out.append(
                        f"← tool call - name: {name}, result: {result_state}: {summary}"
                    )
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
        "Memory Center: /memory list durable, search, show, why, timeline, "
        "graph, pin, archive, restore or forget. Existing task/scratchpad/"
        "context-layer and snapshot commands remain available."
    ),
)
def memory_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    try:
        parts = shlex.split(args or "")
    except ValueError as exc:
        return CommandResult(ok=False, message=f"Invalid /memory arguments: {exc}")
    sub = parts[0].lower() if parts else "status"
    rest_parts = parts[1:]
    rest = rest_parts[0] if rest_parts else ""
    rest_text = " ".join(rest_parts).strip()

    if sub in ("status", ""):
        return _status(session, allow_prompt)
    if sub == "list":
        if rest.lower() == "saved":
            return _list_saved_memory(session, allow_prompt)
        if rest.lower() in {"durable", "ledger", "memories"}:
            return _durable_list(
                session, " ".join(rest_parts[1:]).strip(), allow_prompt
            )
        return _list(session, rest, allow_prompt)
    if sub in {"durable", "search"}:
        return _durable_list(session, rest_text, allow_prompt)
    if sub == "show":
        return _durable_show(session, rest, allow_prompt)
    if sub == "why":
        receipt_id = "" if rest.lower() in {"", "last"} else rest
        return _durable_why(session, receipt_id, allow_prompt)
    if sub == "timeline":
        if not rest:
            return CommandResult(ok=False, message="Usage: /memory timeline <uuid>")
        try:
            item = _durable_service(session).get_for_session(session, rest)
        except Exception as exc:
            return CommandResult(ok=False, message=f"Memory lookup failed: {exc}")
        if item is None:
            return CommandResult(ok=False, message=f"Durable memory {rest!r} not found.")
        events = _durable_service(session).ledger.events(
            memory_id=item.id, limit=200
        )
        if allow_prompt and _console(session) is not None:
            _console(session).print_json(data=events)
        return CommandResult(
            ok=True, message=f"{len(events)} memory events", data={"events": events}
        )
    if sub == "graph":
        if not rest:
            return CommandResult(ok=False, message="Usage: /memory graph <uuid>")
        try:
            item = _durable_service(session).get_for_session(session, rest)
            if item is None:
                return CommandResult(
                    ok=False, message=f"Durable memory {rest!r} not found."
                )
            graph = _durable_service(session).ledger.graph(item.id)
        except Exception as exc:
            return CommandResult(ok=False, message=f"Memory graph failed: {exc}")
        if allow_prompt and _console(session) is not None:
            _console(session).print_json(data=graph)
        return CommandResult(ok=True, message=f"Memory graph for {rest}", data=graph)
    if sub == "edit":
        if len(rest_parts) < 2:
            return CommandResult(
                ok=False, message="Usage: /memory edit <uuid> <new statement>"
            )
        memory_id = rest_parts[0]
        statement = " ".join(rest_parts[1:]).strip()
        try:
            resolved = _durable_service(session).get_for_session(session, memory_id)
            if resolved is None:
                return CommandResult(
                    ok=False, message=f"Durable memory {memory_id!r} not found."
                )
            memory_id = resolved.id
            item = _durable_service(session).revise_for_session(
                session,
                memory_id,
                {"statement": statement},
                actor="user",
                reason="TUI /memory edit",
            )
        except Exception as exc:
            return CommandResult(ok=False, message=f"Memory edit failed: {exc}")
        return CommandResult(
            ok=True,
            message=f"Memory {memory_id} revised to v{item.version}",
            data={"memory": item.to_dict()},
        )
    if sub in {"pin", "unpin", "archive", "restore", "forget"}:
        return _durable_action(session, sub, rest, allow_prompt)
    if sub == "policy":
        data = {
            key: session.variables.get(key)
            for key in (
                "durable_memory_enabled",
                "durable_memory_auto_capture",
                "durable_memory_max_items",
                "durable_memory_token_budget",
                "durable_memory_default_scope",
                "durable_memory_show_receipts",
            )
        }
        data["scopes"] = _durable_service(session).resolve_context(session).eligible()
        if allow_prompt and _console(session) is not None:
            _console(session).print_json(data=data)
        return CommandResult(ok=True, message="Durable memory policy", data=data)
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
        message=(
            f"Unknown subcommand {sub!r}. Usage: /memory "
            "[status|list durable|search|show|why|timeline|graph|edit|pin|"
            "archive|restore|forget|policy|clear|save|load]"
        )
    )
