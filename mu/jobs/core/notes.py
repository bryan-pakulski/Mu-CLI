"""Job notes: the human-readable story of a job (the Notes tab).

Raw job events are written for machines (leases, worktrees, tool previews);
notes are what a person wants to read: what each stage concluded, what it is
waiting on, and what you did. Notes are derived from events at read time, so
every job, old or new, gets them with no migration. The words come from the
agents themselves where they wrote them: plan and review return ``headline``
and ``notes`` fields, and the implement stage ends its final message with a
``HEADLINE:`` / ``NOTES:`` hand-off (``parse_handoff``). Where an agent wrote
none, the first sentence of its summary stands in.

A note: ``{id, at, stage, tone, title, headline, bullets, meta, count, tab}``.
Consecutive notes with the same fold key (seven pauses for tool approval, two
identical check runs) collapse into one with ``count``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Hand-off lines the implement stage is asked to end its final message with.
HANDOFF_INSTRUCTIONS = (
    "End your final message with this hand-off, in plain English, for the human tracking the job:\n"
    "HEADLINE: <one sentence: what you changed, or why you could not>\n"
    "NOTES:\n"
    "- <up to 3 short bullets: notable decisions, anything left undone or unverified>"
)

# Plan/review JSON fields with the same purpose (appended to their schemas).
SCHEMA_FIELDS = ('"headline": "one plain-English sentence for the human tracking the job", '
                 '"notes": ["up to 3 short bullets a human should know"]')

_HEADLINE_RE = re.compile(r"^[ \t>*_#-]*headline[*_]*\s*:[*_]*\s*(?P<text>.+?)\s*$", re.I | re.M)
_NOTES_RE = re.compile(r"^[ \t>*_#-]*notes[*_]*\s*:[*_]*\s*$", re.I | re.M)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(?P<text>.+?)\s*$")
# Emphasis markers only: backticks, and ** / __ pairs. A lone * or _ is content (node_modules, s/.*/).
_MD_RE = re.compile(r"`+|(?<!\w)(\*\*|__)|(\*\*|__)(?!\w)")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`'\"(])")

HEADLINE_CHARS = 240
BULLET_CHARS = 280
MAX_BULLETS = 4

# Event types notes are built from (everything else is machine detail).
NOTE_EVENTS = (
    "job_created", "plan_created", "plan_rejected", "plan_version_approved", "plan_saved",
    "attempt_finished", "checks_completed", "continue_unverified", "review_completed", "command",
    "status_changed", "limit_reached", "stage_context_overflow", "environment_repair",
    "auto_answered", "auto_permission", "harness_usage_limit", "pr_merged", "pr_closed",
    "repush_requested", "push_override", "base_merged", "evidence_added",
)

# Events the Cost & audit timeline leaves out (the Session and Checks tabs show them).
AUDIT_EXCLUDED = frozenset({"sandbox_event", "checks_output"})

# Human commands worth a line of their own. Plan approval, continue-unverified and tool
# approvals have their own events or fold into the note they answer.
_COMMANDS = {
    "resume": "You resumed the job",
    "retry": "You retried the job",
    "cancel": "You cancelled the job",
    "recheck": "You re-ran the checks",
    "reprompt": "You asked for fixes",
    "restartStage": "You restarted the stage",
    "raiseLimit": "You raised the limit",
    "message": "You messaged the agent",
    "push": "You pushed the branch",
    "repush": "You pushed again",
    "markDone": "You marked the job done",
}

_SEVERITY_ORDER = ("blocker", "major", "minor", "nit")
_VERDICT_TEXT = {"ready": "ready to merge", "ready_with_nits": "ready, with nits",
                 "needs_work": "needs work", "do_not_merge": "do not merge"}
_VERDICT_TONE = {"ready": "ok", "ready_with_nits": "ok", "needs_work": "attention", "do_not_merge": "danger"}


# ------------------------------------------------------------------ text helpers

def clean(text: Any, limit: int = HEADLINE_CHARS, *, markdown: bool = True) -> str:
    """One line of plain text: markdown emphasis stripped, whitespace collapsed, clipped."""
    value = str(text or "")
    value = " ".join((_MD_RE.sub("", value) if markdown else value).split())
    if len(value) > limit:
        value = value[: limit - 1].rstrip(" ,;:") + "…"
    return value


def first_sentence(text: Any, limit: int = HEADLINE_CHARS) -> str:
    """The first sentence (or line) of ``text``: the stand-in headline for agents that wrote none."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        # A structured stage reply: its summary carries the prose.
        from ..stages import extract_json
        data = extract_json(raw)
        raw = str(data.get("headline") or data.get("summary") or "").strip() or raw
    line = next((l for l in raw.splitlines() if l.strip() and not l.lstrip().startswith(("#", "```"))), raw)
    return clean(_SENTENCE_RE.split(line.strip(), maxsplit=1)[0], limit)


def bullets(values: Any, *, limit: int = MAX_BULLETS) -> List[str]:
    out: List[str] = []
    for value in values if isinstance(values, (list, tuple)) else []:
        text = clean(value, BULLET_CHARS)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def parse_handoff(text: Any) -> Tuple[str, List[str]]:
    """``(headline, notes)`` from a final message ending with ``HEADLINE:`` / ``NOTES:``.

    The last ``HEADLINE:`` wins (an agent may quote the instructions earlier);
    ``("", [])`` when the message has none."""
    raw = str(text or "")
    matches = list(_HEADLINE_RE.finditer(raw))
    if not matches:
        return "", []
    last = matches[-1]
    headline = clean(last.group("text"))
    notes: List[str] = []
    rest = raw[last.end():]
    marker = _NOTES_RE.search(rest)
    if marker:
        for line in rest[marker.end():].splitlines():
            if not line.strip():
                if notes:
                    break
                continue
            m = _BULLET_RE.match(line)
            if not m:
                break
            notes.append(m.group("text"))
    return headline, bullets(notes)


def summary_headline(data: Dict[str, Any]) -> Tuple[str, List[str]]:
    """``(headline, notes)`` of a plan or review payload, falling back to its summary."""
    data = data if isinstance(data, dict) else {}
    headline = clean(data.get("headline")) or first_sentence(data.get("summary"))
    return headline, bullets(data.get("notes"))


def _first_line(value: Any, limit: int = 140) -> str:
    line = next((l for l in str(value or "").splitlines() if l.strip()), "")
    return clean(line, limit, markdown=False)


def tool_summary(tool_name: str, args: Any, *, limit: int = 160) -> str:
    """A one-line view of a tool call: ``$ git diff --stat``, ``edit src/x.py``, ``grep "foo" src``."""
    name = str(tool_name or "tool")
    if isinstance(args, str):
        return f"{name} {_first_line(args, limit)}".strip()
    args = args if isinstance(args, dict) else {}
    command = args.get("command") or args.get("cmd")
    if command:
        text = " ".join(command) if isinstance(command, list) else str(command)
        more = " …" if len([l for l in text.splitlines() if l.strip()]) > 1 else ""
        return clean("$ " + _first_line(text, limit) + more, limit + 4, markdown=False)
    path = (args.get("filename") or args.get("file_path") or args.get("path") or args.get("notebook_path") or "")
    files = args.get("files") if isinstance(args.get("files"), list) else []
    target = str(path).replace("/workspace/", "") or ", ".join(str(f).replace("/workspace/", "") for f in files[:3])
    query = args.get("pattern") or args.get("query") or args.get("url") or args.get("description") or args.get("prompt")
    verb = {"read_file": "read", "write_file": "write", "edit_file": "edit", "apply_patch": "patch",
            "list_dir": "ls", "search_for_string": "grep", "glob": "glob"}.get(name, name)
    parts = [verb]
    if query:
        parts.append(f'"{clean(query, 80)}"')
    if target:
        parts.append(target)
    return clean(" ".join(parts), limit, markdown=False)


# ------------------------------------------------------------------ note builders

def _note(event, *, stage: str, title: str, tone: str = "neutral", headline: str = "",
          items: Iterable[str] = (), meta: str = "", tab: str = "", fold: str = "") -> Dict[str, Any]:
    return {"id": f"e{event.id}", "at": float(event.created_at or 0.0), "stage": stage, "tone": tone,
            "title": title, "headline": headline, "bullets": [b for b in items if b], "meta": meta,
            "count": 1, "tab": tab, "fold": fold}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _plan(event) -> Dict[str, Any]:
    p = event.payload or {}
    headline, notes = summary_headline(p)
    steps, criteria = len(p.get("steps") or []), len(p.get("acceptance_criteria") or [])
    assumptions = [r[len("Assumption: "):] for r in p.get("risks") or [] if str(r).startswith("Assumption: ")]
    meta = " · ".join(x for x in (_plural(steps, "step") if steps else "",
                                       _plural(criteria, "criterion").replace("criterions", "criteria") if criteria else "",
                                       _plural(len(assumptions), "assumption") if assumptions else "") if x)
    return _note(event, stage="plan", title="Plan ready", tone="ok" if p.get("parsed", True) else "attention",
                 headline=headline, items=notes or bullets(assumptions, limit=2), meta=meta, tab="plan")


def _attempt(event, attempts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    p = event.payload or {}
    attempt = attempts.get(str(p.get("attempt_id") or ""))
    meta_info = dict(getattr(attempt, "metadata", None) or {})
    number = getattr(attempt, "number", 0)
    label = f"attempt {number}" if number else ""
    text = str((meta_info.get("agent_result") or {}).get("text") or "")
    status = str(event.reason or getattr(attempt, "status", "") or "")
    cost = float(p.get("cost_usd") or 0.0)
    meta = " · ".join(x for x in (label, f"${cost:.2f}" if cost else "") if x)
    if status == "completed":
        headline, notes = parse_handoff(text)
        return _note(event, stage="implement", title="Implementation done", tone="ok",
                     headline=headline or first_sentence(text), items=notes, meta=meta, tab="diff")
    if status == "needs_human":
        attention = dict(meta_info.get("attention") or {})
        tool = str(attention.get("tool_name") or "")
        if tool:
            return _note(event, stage="implement", title="Paused for tool approval", tone="attention",
                         headline=("The agent stopped to ask before running a command." if tool.lower() == "bash"
                                   else f"The agent stopped to ask before using {tool}."),
                         items=[tool_summary(tool, attention.get("tool_args"))], meta=meta,
                         tab="session", fold="implement:approval")
        detail = str(meta_info.get("attention_detail") or getattr(attempt, "error", "") or "")
        return _note(event, stage="implement", title="Implementation paused", tone="attention",
                     headline=first_sentence(detail) or "The implement stage is waiting for you.", meta=meta,
                     tab="session")
    if status in ("cancelled",):
        return _note(event, stage="implement", title="Implementation cancelled", meta=meta)
    error = str(p.get("error") or getattr(attempt, "error", "") or "")
    return _note(event, stage="implement", title="Implementation failed", tone="danger",
                 headline=first_sentence(error) or first_sentence(text) or status, meta=meta, tab="diagnostics")


def _checks(event) -> Dict[str, Any]:
    p = event.payload or {}
    total, passed, failed = int(p.get("checks_total") or 0), int(p.get("checks_passed") or 0), int(p.get("checks_failed") or 0)
    unrunnable = int(p.get("unrunnable") or 0)
    meta = f"run {p.get('run')}" if p.get("run") else ""
    if p.get("verified") is False or unrunnable:
        causes = bullets([s.get("cause") or s.get("suite") for s in p.get("unrunnable_suites") or [] if isinstance(s, dict)])
        hint = next((s.get("hint") for s in p.get("unrunnable_suites") or [] if isinstance(s, dict) and s.get("hint")), "")
        return _note(event, stage="checks", title="Checks couldn't run", tone="attention",
                     headline=f"{unrunnable or total} of {total or unrunnable} checks could not run, so the change is unverified.",
                     items=causes + bullets([hint], limit=1), meta=meta, tab="checks",
                     fold=f"checks:unverified:{unrunnable}:{total}")
    if p.get("passed"):
        return _note(event, stage="checks", title="Checks passed", tone="ok",
                     headline=f"All {_plural(total, 'check')} passed." if total else "Checks passed.",
                     meta=meta, tab="checks", fold="checks:passed")
    new, pre = int(p.get("new_failures") or 0), int(p.get("preexisting_failures") or 0)
    detail = [x for x in (f"{new} new failure{'s' if new != 1 else ''}" if new else "",
                          f"{pre} already failing before this change" if pre else "") if x]
    return _note(event, stage="checks", title="Checks failed", tone="danger" if new or not pre else "attention",
                 headline=f"{failed} of {total} checks failed." if total else "Checks failed.",
                 items=detail, meta=meta, tab="checks")


def _review(event) -> Dict[str, Any]:
    p = event.payload or {}
    verdict = str(p.get("verdict") or event.reason or "")
    headline, notes = summary_headline(p)
    if not notes:
        open_findings = [f for f in p.get("findings") or [] if isinstance(f, dict) and f.get("disposition", "open") == "open"]
        open_findings.sort(key=lambda f: _SEVERITY_ORDER.index(f.get("severity")) if f.get("severity") in _SEVERITY_ORDER else 9)
        notes = bullets([f"{str(f.get('severity') or '').capitalize()}: {f.get('title') or ''}" for f in open_findings])
    counts = p.get("counts") or {}
    tally = ", ".join(f"{counts[s]} {s}" for s in _SEVERITY_ORDER if counts.get(s))
    meta = " · ".join(x for x in (f"readiness {p['merge_readiness']}/100" if p.get("merge_readiness") is not None else "",
                                       tally) if x)
    return _note(event, stage="review", title=f"Review: {_VERDICT_TEXT.get(verdict, verdict or 'report ready')}",
                 tone=_VERDICT_TONE.get(verdict, "neutral"), headline=headline, items=notes, meta=meta, tab="review")


def _command(event) -> Optional[Dict[str, Any]]:
    p = event.payload or {}
    name = str(p.get("command") or event.reason or "")
    title = _COMMANDS.get(name)
    if not title:
        return None
    args = p.get("args") if isinstance(p.get("args"), dict) else {}
    guidance = clean(args.get("guidance") or args.get("text") or args.get("message") or "", BULLET_CHARS)
    ids = [str(i) for i in args.get("finding_ids") or []]
    headline = f"Findings: {', '.join(ids)}." if ids else ""
    return _note(event, stage="you", title=title, tone="neutral", headline=headline,
                 items=[f"“{guidance}”"] if guidance else [], fold=f"you:{name}" if name == "message" else "")


def _status(event) -> Optional[Dict[str, Any]]:
    p = event.payload or {}
    state = str((p.get("core_state") or {}).get("state") or "")
    reason = clean(event.reason, BULLET_CHARS)
    if state == "failed":
        return _note(event, stage="job", title="Job failed", tone="danger", headline=reason, tab="diagnostics")
    if state == "cancelled":
        return _note(event, stage="job", title="Job cancelled", headline=reason)
    if state == "pushed":
        url = str(p.get("url") or (p.get("pr") or {}).get("url") or "")
        return _note(event, stage="push", title="Pushed", tone="ok", headline=reason, items=[url] if url else [],
                     fold="push:pushed")
    if state == "done":
        return _note(event, stage="job", title="Job done", tone="ok", headline=reason, fold="job:done")
    return None


def _event_note(event, attempts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    kind, p = event.event_type, event.payload or {}
    if kind == "job_created":
        return _note(event, stage="job", title="Job created")
    if kind == "plan_created":
        return _plan(event)
    if kind == "plan_rejected":
        feedback = clean(p.get("feedback"), BULLET_CHARS)
        return _note(event, stage="you", title="You asked for a new plan", items=[f"“{feedback}”"] if feedback else [])
    if kind == "plan_saved":
        return _note(event, stage="you", title=f"You edited the plan (v{p.get('version')})" if p.get("version") else "You edited the plan",
                     tab="plan", fold="you:plan_saved")
    if kind == "plan_version_approved":
        return _note(event, stage="you", title=f"You approved plan v{p.get('version') or 1}", tone="ok", tab="plan")
    if kind == "attempt_finished":
        return _attempt(event, attempts)
    if kind == "checks_completed":
        return _checks(event)
    if kind == "continue_unverified":
        return _note(event, stage="you", title="You continued without verified checks", tone="attention")
    if kind == "review_completed":
        return _review(event)
    if kind == "command":
        return _command(event)
    if kind == "status_changed":
        return _status(event)
    if kind == "limit_reached":
        return _note(event, stage=str(p.get("stage") or "job"), title="Limit reached", tone="attention",
                     headline=clean(event.reason or p.get("message")))
    if kind == "stage_context_overflow":
        stage = str(p.get("stage") or "")
        return _note(event, stage=stage or "job", title="Context ran out",
                     headline=f"The {stage or 'stage'} agent ran out of model context and restarted with a fresh conversation; "
                              "its changes were kept.", tone="attention")
    if kind == "environment_repair" and p.get("phase") == "finished":
        outcome = str(p.get("outcome") or "")
        return _note(event, stage="checks", title="Environment repair " + (outcome or "finished"),
                     tone="ok" if outcome == "repaired" else "attention", headline=clean(event.reason), tab="checks")
    if kind == "auto_answered":
        stage = str(p.get("stage") or "")
        return _note(event, stage=stage or "job", title="Agent question answered automatically",
                     headline="Jobs run unattended, so the agent was told to use its best judgement.",
                     items=[clean(p.get("question"), BULLET_CHARS)], tab="session", fold=f"auto_answered:{stage}")
    if kind == "auto_permission" and p.get("decision") == "deny":
        stage = str(p.get("stage") or "")
        return _note(event, stage=stage or "job", title=f"Write blocked in the {stage or 'read-only'} stage",
                     headline=clean(p.get("message")) or f"{p.get('tool_name') or 'A tool'} was denied automatically.",
                     items=[clean(p.get("tool_name"))], tab="session", fold=f"auto_deny:{stage}")
    if kind == "harness_usage_limit":
        return _note(event, stage=str(p.get("stage") or "job"), title="Agent usage limit reached", tone="attention",
                     headline=clean(event.reason))
    if kind == "pr_merged":
        return _note(event, stage="push", title="PR merged", tone="ok", headline=clean(event.reason))
    if kind == "pr_closed":
        return _note(event, stage="push", title="PR closed without merging", tone="attention", headline=clean(event.reason))
    if kind in ("repush_requested", "push_override"):
        return _note(event, stage="you", title="You pushed again" if kind == "repush_requested" else "You pushed past a gate",
                     tone="neutral" if kind == "repush_requested" else "attention", headline=clean(event.reason))
    if kind == "evidence_added":
        count = int(p.get("count") or 0)
        stage = str(p.get("stage") or "")
        return _note(event, stage=stage or "job", title=f"Evidence captured ({count} file{'s' if count != 1 else ''})",
                     tone="ok", headline=f"The {stage or 'agent'} stage saved what it observed while testing.",
                     items=bullets(p.get("captions"), limit=3), tab="checks", fold=f"evidence:{stage}")
    if kind == "base_merged":
        return _note(event, stage="job", title="Base branch merged in", headline=clean(event.reason))
    return None


def fold(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse runs of notes that share a fold key; human tool approvals between them are absorbed."""
    out: List[Dict[str, Any]] = []
    for note in notes:
        prev = out[-1] if out else None
        if prev and note["fold"] and prev["fold"] == note["fold"]:
            prev["count"] += 1
            prev["at"] = note["at"]
            prev["id"] = note["id"]
            for item in note["bullets"]:
                if item not in prev["bullets"]:
                    prev["bullets"].append(item)
            prev["bullets"] = prev["bullets"][-MAX_BULLETS:]
            if note["meta"]:
                prev["meta"] = note["meta"]
            continue
        out.append(dict(note, bullets=list(note["bullets"])))
    return out


def repeat_less(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A note that repeats an earlier one word for word (the same checks failing for the same
    reasons after a resume) keeps its title but drops the repeated detail."""
    seen: Dict[str, Tuple[str, List[str]]] = {}
    for note in notes:
        key = note.get("fold") or ""
        if not key:
            continue
        if seen.get(key) == (note["headline"], note["bullets"]) and note["bullets"]:
            note["bullets"] = []
            note["headline"] = (note["headline"] + " Same as before.").strip()
        else:
            seen[key] = (note["headline"], list(note["bullets"]))
    return notes


def job_notes(service, job) -> List[Dict[str, Any]]:
    """The job's notes, oldest first."""
    events = service.store.list_events_by_type(job.id, NOTE_EVENTS)
    try:
        attempts = {a.id: a for a in service.attempts(job.id)}
    except Exception:  # noqa: BLE001 - notes degrade to event text without attempt detail
        attempts = {}
    notes = [n for n in (_event_note(e, attempts) for e in events) if n]
    out = repeat_less(fold(notes))
    for note in out:
        note.pop("fold", None)
    return out
