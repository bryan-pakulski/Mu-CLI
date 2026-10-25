"""Research workflow command — the session's research ledger.

Subcommands:

    /research                       — status snapshot
    /research <query>               — run a research query (flips to research mode)
    /research questions             — the question tree with status and claim counts
    /research claims [qid]          — recorded claims with confidence and evidence
    /research gaps                  — questions without claims, contested claims, unread leads
    /research sources [filters]     — list sources w/ state and credibility
    /research show <id|sN>          — full record for one source
    /research bibliography [--cited] [qid] — the compiled markdown bibliography
    /research stats                 — counts by source type + averages
    /research topic [name]          — show or select the current root question
    /research topics                — list the groups that have read sources
    /research export <dir>          — write source_register / evidence_log / questions .md
    /research clear                 — wipe the current research trail
    /research new <query>           — start a fresh research trail

Filters for `/research sources`:
    --state <candidate|read|cited>  default: read and cited
    --type <web|academic|social|forum|news|documentation|other>
    --min <0.0..1.0>                minimum credibility score
    --query <substring>             case-insensitive match on title/url
    --topic <substring>             case-insensitive match on question/topic
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import CommandResult, command


# ----------------------------------------------- helpers


def _refresh_hud(session: Any) -> None:
    try:
        from mucli import refresh_memory_hud

        refresh_memory_hud(session, getattr(session, "ui", None))
    except ImportError:
        pass


def _engine(session: Any = None):
    """This session's ResearchState (a private default when there is none)."""
    from mu.research import ResearchState, get_citation_manager

    state = getattr(getattr(session, "session_manager", None), "research", None)
    return state if isinstance(state, ResearchState) else get_citation_manager()


def _save(session: Any) -> None:
    """Persist explicit research mutations immediately."""
    manager = getattr(session, "session_manager", None)
    if manager is None or not hasattr(manager, "save_history"):
        return
    manager.save_history(getattr(session, "folder_context", None))


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


def _table(title: str, columns, rows, session: Any) -> None:
    console = _console(session)
    if console is None or not rows:
        return
    try:
        from rich import box
        from rich.table import Table
        from rich.text import Text

        table = Table(title=title, box=box.SIMPLE_HEAD)
        for name, style, justify in columns:
            table.add_column(name, style=style, justify=justify)
        for row in rows:
            table.add_row(*(Text(str(cell)) for cell in row))
        console.print(table)
    except Exception:
        pass


def _research_tool_names() -> List[str]:
    return [
        "research_question",
        "web_search",
        "arxiv_search",
        "doi_resolve",
        "reddit_search",
        "stackoverflow_search",
        "hackernews_search",
        "url_grounding",
        "research_fetch",
        "read_document",
        "assess_source",
        "record_claim",
        "add_evidence",
        "research_status",
        "research_report",
        "research_export",
    ]


def _source_to_dict(state, source) -> Dict[str, Any]:
    return {
        "id": source.citation_id,
        "source_id": source.candidate_id,
        "state": source.state,
        "title": source.title,
        "url": source.url,
        "type": source.source_type.value,
        "origin": source.origin,
        "topic": state.group_label(source),
        "question_ids": list(source.question_ids),
        "credibility": round(source.credibility_score, 3),
        "assessed": source.assessed,
        "authors": list(source.authors),
        "date": source.date,
        "site": source.site,
        "accessed": source.accessed_date,
        "snippet": source.snippet,
        "metadata": dict(source.metadata or {}),
    }


def _parse_source_filters(rest: str):
    """`--state S --type X --min 0.6 --query foo --topic bar` → (filters, error)."""
    tokens = rest.split() if rest else []
    filters: Dict[str, Any] = {"state": None, "type": None, "min": None, "query": None, "topic": None}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        key = tok[2:] if tok.startswith("--") else ""
        if key in filters and i + 1 < len(tokens):
            value = tokens[i + 1]
            if key == "min":
                try:
                    filters["min"] = float(value)
                except ValueError:
                    return None, f"--min must be a number, got {value!r}"
            else:
                filters[key] = value.lower()
            i += 2
        else:
            return None, f"unknown flag {tok!r} — use --state/--type/--min/--query/--topic"
    if filters["state"] and filters["state"] not in ("candidate", "read", "cited"):
        return None, "--state must be candidate, read or cited"
    return filters, None


# ----------------------------------------------- subcommands


def _status(session: Any, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    coverage = state.coverage()
    sources = state.numbered_sources()
    by_type: Dict[str, int] = {}
    for src in sources:
        by_type[src.source_type.value] = by_type.get(src.source_type.value, 0) + 1
    avg_cred = sum(s.credibility_score for s in sources) / len(sources) if sources else 0.0
    active_mode = str(session.variables.get("agent_mode", "default"))
    counts = coverage["sources"]
    statuses: Dict[str, int] = {}
    for row in coverage["questions"]:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    msg = (
        f"Research mode: {active_mode} · {len(coverage['questions'])} question(s) · "
        f"{coverage['claims']} claim(s) · sources {counts['candidate']} candidate / "
        f"{counts['read']} read / {counts['cited']} cited · current: {state.get_current_topic()}"
    )
    _emit(session, msg, allow_prompt)
    return CommandResult(
        ok=True,
        message=msg,
        data={
            "current_mode": active_mode,
            "current_topic": state.get_current_topic(),
            "current_question": coverage["current_question"],
            "available_tools": _research_tool_names(),
            "source_count": len(sources),
            "sources_by_state": counts,
            "question_count": len(coverage["questions"]),
            "question_statuses": statuses,
            "claim_count": coverage["claims"],
            "gaps": coverage["open_without_claims"],
            "by_type": by_type,
            "avg_credibility": round(avg_cred, 3),
        },
    )


def _questions(session: Any, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    rows = state.coverage()["questions"]
    if allow_prompt:
        _table(
            f"Research questions ({len(rows)})",
            [("ID", "wash.muted", "left"), ("Status", "wash.accent", "left"),
             ("Claims", "wash.ink", "right"), ("Origins", "wash.ink", "right"),
             ("Question", "wash.ink", "left")],
            [(r["id"], r["status"], r["claims"], r["origins"], ("  " * r["id"].count(".")) + r["text"])
             for r in rows],
            session,
        )
        if not rows:
            _emit(session, "No research questions yet.", allow_prompt)
    return CommandResult(ok=True, message=f"{len(rows)} question(s).", data={"questions": rows})


def _claim_dict(state, claim) -> Dict[str, Any]:
    return {
        "id": claim.id,
        "question_id": claim.question_id,
        "text": claim.text,
        "kind": claim.kind,
        "confidence": state.claim_confidence(claim),
        "contested": state.claim_contested(claim),
        "origins": state.claim_origins(claim),
        "evidence": [ev.to_dict() for ev in claim.evidence],
    }


def _claims(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    qid = rest.strip() or None
    if qid and state.get_question(qid) is None:
        msg = f"No question {qid!r}."
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)
    claims = [
        c for c in state.claims()
        if not qid or c.question_id == qid or c.question_id.startswith(qid + ".")
    ]
    items = [_claim_dict(state, c) for c in claims]
    if allow_prompt:
        _table(
            f"Claims ({len(items)})",
            [("ID", "wash.muted", "left"), ("Q", "wash.muted", "left"),
             ("Confidence", "wash.accent", "left"), ("Sources", "wash.info", "left"),
             ("Claim", "wash.ink", "left")],
            [(c["id"], c["question_id"],
              c["confidence"] + (" · contested" if c["contested"] else ""),
              " ".join(f"[^{e['citation_id']}]{'✗' if e['stance'] == 'contradicts' else ''}"
                       for e in c["evidence"]),
              c["text"]) for c in items],
            session,
        )
        if not items:
            _emit(session, "No claims recorded yet.", allow_prompt)
    return CommandResult(ok=True, message=f"{len(items)} claim(s).", data={"question_id": qid, "claims": items})


def _gaps(session: Any, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    coverage = state.coverage()
    by_id = {r["id"]: r for r in coverage["questions"]}
    no_claims = [by_id[q] for q in coverage["open_without_claims"]]
    contested = [_claim_dict(state, c) for c in state.claims() if state.claim_contested(c)]
    weak = [_claim_dict(state, c) for c in state.claims() if state.claim_confidence(c) == "low"]
    leads = [
        {"question_id": r["id"], **cand}
        for r in coverage["questions"] if r["status"] != "dropped"
        for cand in r["unread_candidates"]
    ]
    if allow_prompt:
        lines = []
        for r in no_claims:
            lines.append(f"no claims: {r['id']} {r['text']}")
        for c in contested:
            lines.append(f"contested: {c['id']} {c['text']}")
        for c in weak:
            lines.append(f"low confidence: {c['id']} {c['text']}")
        for lead in leads[:10]:
            lines.append(f"unread lead for {lead['question_id']}: {lead['id']} {lead['title']}")
        _emit(session, "\n".join(lines) or "No gaps: every question has claims at medium or better.",
              allow_prompt)
    total = len(no_claims) + len(contested) + len(weak)
    return CommandResult(
        ok=True,
        message=f"{total} gap(s).",
        data={"questions_without_claims": no_claims, "contested": contested,
              "low_confidence": weak, "unread_leads": leads},
    )


def _list_sources(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    from mu.research import SourceType

    state = _engine(session)
    filters, err = _parse_source_filters(rest)
    if err:
        _emit(session, err, allow_prompt, error=True)
        return CommandResult(ok=False, message=err)

    if filters["state"]:
        sources = state.all_sources(filters["state"])
    else:
        sources = state.numbered_sources()
    if filters["topic"]:
        sources = [s for s in sources if filters["topic"] in state.group_label(s).lower()]
    if filters["type"]:
        try:
            target = SourceType(filters["type"])
        except ValueError:
            valid = ", ".join(t.value for t in SourceType)
            msg = f"unknown --type {filters['type']!r}. Valid: {valid}"
            _emit(session, msg, allow_prompt, error=True)
            return CommandResult(ok=False, message=msg)
        sources = [s for s in sources if s.source_type == target]
    if filters["min"] is not None:
        sources = [s for s in sources if s.credibility_score >= filters["min"]]
    if filters["query"]:
        q = filters["query"]
        sources = [s for s in sources if q in (s.title or "").lower() or q in (s.url or "").lower()]
    sources.sort(key=lambda s: (-s.credibility_score, s.citation_id or 10**9, s.sid))

    if allow_prompt:
        _table(
            f"Research sources ({len(sources)})",
            [("ID", "wash.muted", "right"), ("State", "wash.accent", "left"),
             ("Type", "wash.accent", "left"), ("Cred", "wash.ok", "right"),
             ("Title", "wash.ink", "left"), ("URL", "wash.info", "left")],
            [(f"#{s.citation_id}" if s.citation_id else s.candidate_id, s.state, s.source_type.value,
              f"{s.credibility_score:.2f}", (s.title or "")[:60], (s.url or "")[:60])
             for s in sources[:50]],
            session,
        )
        if not sources:
            _emit(session, "No sources match those filters.", allow_prompt)
    return CommandResult(
        ok=True,
        message=f"{len(sources)} source(s).",
        data={"filters": filters, "count": len(sources),
              "sources": [_source_to_dict(state, s) for s in sources]},
    )


def _show_source(session: Any, raw_id: str, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    raw_id = (raw_id or "").strip()
    if not raw_id:
        msg = "Usage: /research show <id|sN>"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)
    source = state.find(raw_id)
    if source is None:
        msg = f"No source {raw_id!r}."
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg, data={"id": raw_id})
    body = _source_to_dict(state, source)
    if allow_prompt:
        _table(
            source.ref,
            [("k", "wash.accent", "left"), ("v", "wash.ink", "left")],
            [(label, value) for label, value in (
                ("Title", body["title"]), ("URL", body["url"]), ("State", body["state"]),
                ("Type", body["type"]), ("Origin", body["origin"]),
                ("Credibility", f"{body['credibility']:.2f}" + ("" if body["assessed"] else " (prior)")),
                ("Authors", ", ".join(body["authors"]) or "—"), ("Published", body["date"] or "—"),
                ("Accessed", body["accessed"]), ("Questions", ", ".join(body["question_ids"]) or "—"),
            )],
            session,
        )
    return CommandResult(ok=True, message=f"Source {source.ref}.", data=body)


def _bibliography(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    tokens = rest.split()
    include = "cited" if "--cited" in tokens else "read"
    rest_tokens = [t for t in tokens if t != "--cited"]
    qid = rest_tokens[0] if rest_tokens else None
    if qid and state.get_question(qid) is None:
        # A free-text argument names a root question by its text.
        match = next((q for q in state.questions() if q.text.lower() == " ".join(rest_tokens).lower()), None)
        qid = match.id if match else qid
    body = state.compile_bibliography(include=include, question_id=qid) or ""
    if allow_prompt:
        console = _console(session)
        if console is not None and body:
            try:
                from rich.markdown import Markdown
                from mu.ui.theme.pygments import syntax_theme

                console.print(Markdown(body, code_theme=syntax_theme()))
            except Exception:
                try:
                    console.print(body, markup=False)
                except Exception:
                    pass
        elif not body:
            _emit(session, "Bibliography is empty — no sources read yet.", allow_prompt)
    return CommandResult(
        ok=True,
        message=("ok" if body else "empty"),
        data={"bibliography": body, "question_id": qid, "include": include,
              "source_count": state.source_count},
    )


def _stats(session: Any, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    sources = state.numbered_sources()
    by_type: Dict[str, int] = {}
    total_cred = 0.0
    for src in sources:
        by_type[src.source_type.value] = by_type.get(src.source_type.value, 0) + 1
        total_cred += src.credibility_score
    avg = (total_cred / len(sources)) if sources else 0.0
    by_state = {st: len(state.all_sources(st)) for st in ("candidate", "read", "cited")}
    levels = {"high": 0, "medium": 0, "low": 0}
    for c in state.claims():
        levels[state.claim_confidence(c)] += 1
    origins = {s.origin for s in sources}
    if allow_prompt:
        _table(
            "Research stats",
            [("Metric", "wash.accent", "left"), ("Value", "wash.ink", "left")],
            [("Sources read", len(sources)), ("Independent origins", len(origins)),
             ("Average credibility", f"{avg:.2f}"),
             ("By state", ", ".join(f"{k}={v}" for k, v in by_state.items())),
             ("By type", ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "—"),
             ("Claim confidence", ", ".join(f"{k}={v}" for k, v in levels.items()))],
            session,
        )
    return CommandResult(
        ok=True,
        message=f"{len(sources)} source(s), avg credibility {avg:.2f}.",
        data={"count": len(sources), "by_type": by_type, "by_state": by_state,
              "origins": len(origins), "claim_confidence": levels, "avg_credibility": round(avg, 3)},
    )


def _topic(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    name = rest.strip()
    if not name:
        current = state.get_current_topic()
        msg = f"Current research question: {current}"
        _emit(session, msg, allow_prompt)
        return CommandResult(ok=True, message=msg,
                             data={"current_topic": current, "topics": state.list_topics()})
    previous = state.get_current_topic()
    state.set_topic(name)
    _save(session)
    msg = f"Research question: {previous!r} -> {name!r}"
    _emit(session, msg, allow_prompt)
    q = state.current_question()
    return CommandResult(ok=True, message=msg,
                         data={"previous_topic": previous, "current_topic": name,
                               "question_id": q.id if q else None})


def _topics(session: Any, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    topics = state.list_topics()
    counts: Dict[str, int] = {}
    for src in state.numbered_sources():
        label = state.group_label(src)
        counts[label] = counts.get(label, 0) + 1
    if allow_prompt:
        _table("Research topics", [("Topic", "wash.accent", "left"), ("Sources", "wash.ink", "right")],
               [(t, counts.get(t, 0)) for t in topics], session)
    return CommandResult(ok=True, message=f"{len(topics)} topic(s).",
                         data={"topics": topics, "counts": counts,
                               "current_topic": state.get_current_topic()})


def _export(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    from mu.research.report import export_files
    from mu.tools.capabilities import session_type_from_context
    from mu.tools.file.handlers import write_file

    directory = rest.strip()
    if not directory:
        msg = "Usage: /research export <dir>"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)
    session_type = session_type_from_context(session)
    if session_type in ("chat", "orchestration"):
        msg = "Export needs a workspace or container session (chat sessions have no filesystem)."
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)
    folder_context = getattr(session, "folder_context", None)
    written: List[str] = []
    for name, body in export_files(_engine(session)).items():
        result = write_file(os.path.join(directory, name), body, folder_context, session_type=session_type)
        if not str(result).startswith("Successfully"):
            _emit(session, str(result), allow_prompt, error=True)
            return CommandResult(ok=False, message=str(result), data={"written": written})
        written.append(name)
    msg = f"Exported {', '.join(written)} to {directory}"
    _emit(session, msg, allow_prompt)
    return CommandResult(ok=True, message=msg, data={"dir": directory, "written": written})


def _clear(session: Any, allow_prompt: bool) -> CommandResult:
    state = _engine(session)
    prev = len(state.all_sources())
    claims = len(state.claims())
    state.clear()
    _save(session)
    _emit(session, f"Research trail cleared — dropped {prev} source(s) and {claims} claim(s).", allow_prompt)
    return CommandResult(ok=True, message=f"Cleared {prev} source(s).",
                         data={"cleared": prev, "claims": claims})


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
        "- Read sources before citing them and record claims with quoted evidence.\n"
        "- Clearly separate facts vs inference.\n"
    )
    send_result = session.send_message(prompt)
    return CommandResult(
        ok=bool(send_result.get("ok", True)),
        message="Executed research query.",
        data={"query": query, "send_result": send_result},
    )


# ----------------------------------------------- dispatch


SUBCOMMANDS = ("questions", "claims", "gaps", "sources", "show", "bibliography", "stats",
               "topic", "topics", "export", "clear", "new", "status")


@command(
    "/research",
    help=(
        "Research ledger. Subcommands: status, questions, claims [qid], gaps, "
        "sources [--state S --type X --min N --query Q --topic T], show <id|sN>, "
        "bibliography [--cited] [qid], stats, topic [name], topics, export <dir>, "
        "clear, new <query>. Anything else is treated as a query."
    ),
)
def research_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> Optional[CommandResult]:
    raw = (args or "").strip()

    if not raw:
        return _status(session, allow_prompt)

    head, _, rest = raw.partition(" ")
    sub = head.lower()
    rest = rest.strip()

    if sub == "status":
        return _status(session, allow_prompt)
    if sub == "questions":
        return _questions(session, allow_prompt)
    if sub == "claims":
        return _claims(session, rest, allow_prompt)
    if sub == "gaps":
        return _gaps(session, allow_prompt)
    if sub == "sources":
        return _list_sources(session, rest, allow_prompt)
    if sub == "show":
        return _show_source(session, rest, allow_prompt)
    if sub in ("bibliography", "biblio", "bib"):
        return _bibliography(session, rest, allow_prompt)
    if sub == "stats":
        return _stats(session, allow_prompt)
    if sub == "topic":
        return _topic(session, rest, allow_prompt)
    if sub == "topics":
        return _topics(session, allow_prompt)
    if sub == "export":
        return _export(session, rest, allow_prompt)
    if sub == "clear":
        return _clear(session, allow_prompt)
    if sub == "new":
        if not rest:
            msg = "Usage: /research new <query>"
            _emit(session, msg, allow_prompt, error=True)
            return CommandResult(ok=False, message=msg)
        _clear(session, allow_prompt=False)
        return _run_query(session, rest, allow_prompt)

    # Anything else → query.
    return _run_query(session, raw, allow_prompt)
