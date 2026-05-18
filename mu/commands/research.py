"""Research workflow command — exposes the CitationManager engine.

Subcommands:

    /research                       — status snapshot
    /research <query>               — run a research query (flips to research mode)
    /research sources [filters]     — list every registered source w/ credibility
    /research show <id>             — full record for one source
    /research bibliography          — emit the compiled markdown bibliography
    /research stats                 — counts by source type + averages
    /research clear                 — wipe the citation engine

Filters for `/research sources`:
    --type <web|academic|social|forum|news|documentation|other>
    --min <0.0..1.0>                 minimum credibility score
    --query <substring>              case-insensitive match on title/url
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import CommandResult, command


# ----------------------------------------------- helpers


def _refresh_hud(session: Any) -> None:
    try:
        from mucli import refresh_memory_hud

        refresh_memory_hud(session, getattr(session, "ui", None))
    except ImportError:
        pass


def _engine():
    """The global CitationManager singleton. Lazy-imported so the
    command module doesn't pull in `utils.citation_manager` at module
    load (it's heavier than it needs to be for /help generation)."""
    from utils.citation_manager import SourceType, get_citation_manager

    return get_citation_manager(), SourceType


def _emit(session: Any, body: str, allow_prompt: bool, *, error: bool = False) -> None:
    ui = getattr(session, "ui", None)
    if ui is None or not allow_prompt:
        return
    method = "show_error" if error else "show_info"
    if hasattr(ui, method):
        try:
            getattr(ui, method)(body)
        except Exception:
            pass


def _console(session: Any):
    ui = getattr(session, "ui", None)
    return getattr(ui, "console", None) if ui is not None else None


def _research_tool_names() -> List[str]:
    return [
        "web_search",
        "url_grounding",
        "arxiv_search",
        "doi_resolve",
        "reddit_search",
        "stackoverflow_search",
        "hackernews_search",
        "read_document",
    ]


def _source_to_dict(source) -> Dict[str, Any]:
    """Project a Source dataclass into the JSON-friendly shape every
    subcommand returns in `data`.

    Defensive on `source_type`: historically some call sites passed
    plain strings ("web"). Newer code coerces at registration but
    existing in-memory sources (this session, or pickled state) may
    still carry strings — handle both shapes so /research never
    crashes on legacy data."""
    raw_type = source.source_type
    type_value = getattr(raw_type, "value", None) or str(raw_type)
    return {
        "id": source.id,
        "title": source.title,
        "url": source.url,
        "type": type_value,
        "credibility": round(source.credibility_score, 3),
        "authors": list(source.authors),
        "date": source.date,
        "accessed": source.accessed_date,
        "metadata": dict(source.metadata or {}),
    }


def _parse_source_filters(rest: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[str]]:
    """Parse `--type X --min 0.6 --query foo` style flags. Returns
    (type_filter, min_credibility, query_substring, error). The error
    string is populated when parsing fails — caller short-circuits."""
    tokens = rest.split() if rest else []
    type_filter: Optional[str] = None
    min_credibility: Optional[float] = None
    query: Optional[str] = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--type" and i + 1 < len(tokens):
            type_filter = tokens[i + 1].lower()
            i += 2
        elif tok == "--min" and i + 1 < len(tokens):
            try:
                min_credibility = float(tokens[i + 1])
            except ValueError:
                return None, None, None, f"--min must be a number, got {tokens[i + 1]!r}"
            i += 2
        elif tok == "--query" and i + 1 < len(tokens):
            query = tokens[i + 1].lower()
            i += 2
        else:
            return None, None, None, f"unknown flag {tok!r} — use --type/--min/--query"
    return type_filter, min_credibility, query, None


# ----------------------------------------------- subcommands


def _status(session: Any, allow_prompt: bool) -> CommandResult:
    engine, SourceType = _engine()
    sources = engine.get_all_sources()
    by_type: Dict[str, int] = {}
    for src in sources:
        by_type[src.source_type.value] = by_type.get(src.source_type.value, 0) + 1
    avg_cred = (
        sum(s.credibility_score for s in sources) / len(sources) if sources else 0.0
    )
    active_mode = str(session.variables.get("agent_mode", "default"))

    msg = (
        f"Research mode: {active_mode} · "
        f"{len(sources)} source(s) collected · "
        f"avg credibility {avg_cred:.2f}"
    )
    _emit(session, msg, allow_prompt)
    return CommandResult(
        ok=True,
        message=msg,
        data={
            "current_mode": active_mode,
            "available_tools": _research_tool_names(),
            "source_count": len(sources),
            "by_type": by_type,
            "avg_credibility": round(avg_cred, 3),
            "citation_policy": (
                "Cite external claims with [^n] footnotes; end with "
                "compile_bibliography(). Prefer ≥0.7 credibility for facts."
            ),
        },
    )


def _list_sources(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    engine, SourceType = _engine()

    type_filter, min_cred, query, err = _parse_source_filters(rest)
    if err:
        _emit(session, err, allow_prompt, error=True)
        return CommandResult(ok=False, message=err)

    sources = engine.get_all_sources()

    # Apply filters.
    if type_filter:
        try:
            target = SourceType(type_filter)
        except ValueError:
            valid = ", ".join(t.value for t in SourceType)
            msg = f"unknown --type {type_filter!r}. Valid: {valid}"
            _emit(session, msg, allow_prompt, error=True)
            return CommandResult(ok=False, message=msg)
        sources = [s for s in sources if s.source_type == target]
    if min_cred is not None:
        sources = [s for s in sources if s.credibility_score >= min_cred]
    if query:
        sources = [
            s
            for s in sources
            if query in (s.title or "").lower() or query in (s.url or "").lower()
        ]

    # Sort by credibility desc, then id asc for stability.
    sources.sort(key=lambda s: (-s.credibility_score, s.id))

    if allow_prompt:
        console = _console(session)
        if console is not None and sources:
            try:
                from rich import box
                from rich.table import Table

                table = Table(
                    title=f"Citation sources ({len(sources)})",
                    box=box.SIMPLE,
                )
                table.add_column("ID", style="dim", justify="right")
                table.add_column("Type", style="cyan")
                table.add_column("Cred", style="green", justify="right")
                table.add_column("Title", style="white")
                table.add_column("URL", style="blue")
                from rich.text import Text

                for src in sources[:50]:  # cap table render; data field has everything
                    stars = "★" * int(round(src.credibility_score * 5))
                    cred = f"{stars} {src.credibility_score:.2f}"
                    title = (src.title or "")[:60]
                    url = (src.url or "")[:60]
                    table.add_row(
                        f"#{src.id}",
                        Text(src.source_type.value),
                        cred,
                        Text(title),
                        Text(url),
                    )
                console.print(table)
                if len(sources) > 50:
                    console.print(f"[dim]…and {len(sources) - 50} more (see data payload).[/dim]")
            except Exception:
                pass
        elif console is not None:
            _emit(session, "No sources match those filters.", allow_prompt)

    return CommandResult(
        ok=True,
        message=f"{len(sources)} source(s).",
        data={
            "filters": {"type": type_filter, "min_credibility": min_cred, "query": query},
            "count": len(sources),
            "sources": [_source_to_dict(s) for s in sources],
        },
    )


def _show_source(session: Any, raw_id: str, allow_prompt: bool) -> CommandResult:
    engine, _ = _engine()
    raw_id = (raw_id or "").lstrip("#").strip()
    if not raw_id:
        msg = "Usage: /research show <id>"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)
    try:
        cid = int(raw_id)
    except ValueError:
        msg = f"Source id must be an integer, got {raw_id!r}"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)

    source = engine.get_source(cid)
    if source is None:
        msg = f"No source with id #{cid}."
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg, data={"id": cid})

    body = _source_to_dict(source)
    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                from rich import box
                from rich.table import Table

                from rich.text import Text

                tbl = Table(show_header=False, box=box.SIMPLE)
                tbl.add_column("k", style="cyan")
                tbl.add_column("v")
                for label, value in (
                    ("ID", f"#{body['id']}"),
                    ("Title", body["title"]),
                    ("URL", body["url"]),
                    ("Type", body["type"]),
                    ("Credibility", f"{body['credibility']:.2f}"),
                    ("Authors", ", ".join(body["authors"]) or "—"),
                    ("Published", body["date"] or "—"),
                    ("Accessed", body["accessed"]),
                ):
                    tbl.add_row(label, Text(str(value)))
                console.print(tbl)
                if body["metadata"]:
                    from utils.helpers import safe_markup

                    console.print(f"[dim]metadata:[/dim] {safe_markup(body['metadata'])}")
            except Exception:
                pass
    return CommandResult(ok=True, message=f"Source #{cid}.", data=body)


def _bibliography(session: Any, allow_prompt: bool) -> CommandResult:
    engine, _ = _engine()
    body = engine.compile_bibliography() or ""
    if allow_prompt:
        console = _console(session)
        if console is not None and body:
            try:
                from rich.markdown import Markdown

                console.print(Markdown(body))
            except Exception:
                try:
                    console.print(body, markup=False)
                except Exception:
                    pass
        elif not body:
            _emit(session, "Bibliography is empty — no sources registered yet.", allow_prompt)
    return CommandResult(
        ok=True,
        message=("ok" if body else "empty"),
        data={"bibliography": body, "source_count": engine.source_count},
    )


def _stats(session: Any, allow_prompt: bool) -> CommandResult:
    engine, _ = _engine()
    sources = engine.get_all_sources()
    by_type: Dict[str, int] = {}
    cred_buckets = {"★★★★★": 0, "★★★★": 0, "★★★": 0, "★★": 0, "★": 0, "☆": 0}
    total_cred = 0.0
    for src in sources:
        by_type[src.source_type.value] = by_type.get(src.source_type.value, 0) + 1
        total_cred += src.credibility_score
        score = int(round(src.credibility_score * 5))
        key = "★" * max(1, score) if score >= 1 else "☆"
        cred_buckets[key] = cred_buckets.get(key, 0) + 1
    avg = (total_cred / len(sources)) if sources else 0.0

    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                from rich import box
                from rich.table import Table

                from rich.text import Text

                t = Table(title="Research engine stats", box=box.SIMPLE)
                t.add_column("Metric", style="cyan")
                t.add_column("Value", style="white")
                t.add_row("Sources collected", Text(str(len(sources))))
                t.add_row("Average credibility", Text(f"{avg:.2f}"))
                t.add_row(
                    "By type",
                    Text(", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "—"),
                )
                t.add_row(
                    "Credibility tiers",
                    Text(", ".join(f"{k}={v}" for k, v in cred_buckets.items() if v > 0) or "—"),
                )
                console.print(t)
            except Exception:
                pass

    return CommandResult(
        ok=True,
        message=f"{len(sources)} source(s), avg credibility {avg:.2f}.",
        data={
            "count": len(sources),
            "by_type": by_type,
            "credibility_tiers": cred_buckets,
            "avg_credibility": round(avg, 3),
        },
    )


def _clear(session: Any, allow_prompt: bool) -> CommandResult:
    engine, _ = _engine()
    prev = engine.source_count
    engine.clear()
    _emit(
        session,
        f"Citation engine cleared — dropped {prev} source(s).",
        allow_prompt,
    )
    return CommandResult(
        ok=True,
        message=f"Cleared {prev} source(s).",
        data={"cleared": prev},
    )


def _run_query(session: Any, query: str, allow_prompt: bool) -> CommandResult:
    session.variables["agent_mode"] = "research"
    fc = getattr(session, "folder_context", None)
    session.session_manager.save_history(fc)
    _refresh_hud(session)

    prompt = (
        "Research request:\n"
        f"{query}\n\n"
        "Requirements:\n"
        "- Prefer primary/official sources when possible.\n"
        "- Include explicit source URLs.\n"
        "- Clearly separate facts vs inference.\n"
    )
    send_result = session.send_message(prompt)
    return CommandResult(
        ok=bool(send_result.get("ok", True)),
        message="Executed research query.",
        data={"query": query, "send_result": send_result},
    )


# ----------------------------------------------- dispatch


SUBCOMMANDS = ("sources", "show", "bibliography", "stats", "clear", "status")


@command(
    "/research",
    help=(
        "Research workflow / citation engine. "
        "Subcommands: status, sources [--type X --min N --query Q], "
        "show <id>, bibliography, stats, clear. "
        "Anything else is treated as a query."
    ),
)
def research_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()

    if not raw:
        return _status(session, allow_prompt)

    head, _, rest = raw.partition(" ")
    sub = head.lower()
    rest = rest.strip()

    if sub == "status":
        return _status(session, allow_prompt)
    if sub == "sources":
        return _list_sources(session, rest, allow_prompt)
    if sub == "show":
        return _show_source(session, rest, allow_prompt)
    if sub in ("bibliography", "biblio", "bib"):
        return _bibliography(session, allow_prompt)
    if sub == "stats":
        return _stats(session, allow_prompt)
    if sub == "clear":
        return _clear(session, allow_prompt)

    # Anything else → query.
    return _run_query(session, raw, allow_prompt)
