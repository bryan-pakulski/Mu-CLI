"""Write the missing headlines for jobs that predate the headline contract.

Plan and review stages now return ``headline``/``notes`` and the implement stage ends
with a ``HEADLINE:`` hand-off (core/notes.py). Older jobs have none, so their notes
fall back to the first sentence of a long summary. This asks a small model for the
headline and bullets of every plan, review and finished implement run that lacks them,
and saves them to the job's ``notes.json`` (the event log is never rewritten). A
review's headline is also written into ``review.json``, which the banner and Review tab
read.

    python3 -m mu.jobs.notes_backfill [--dry-run] [--provider anthropic --model claude-haiku-5-5]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional

from .core.notes import WRITTEN_NOTES, bullets, clean, parse_handoff, written_notes

GenerateFn = Callable[[str], str]

DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-haiku-5-5"

_PROMPT = """You write the notes a human skims to follow an automated engineering job.
Below is the {what}. Write its bottom line in plain English, without code or file paths
unless they are essential.

Respond with JSON only:
{{"headline": "one sentence, at most 25 words: {headline_hint}",
  "notes": ["up to 3 short bullets a human should know: risks, anything unverified or left undone"]}}

Job title: {title}

{what_cap}:
{body}
"""

_KINDS = {
    "plan_created": ("plan the planning agent wrote", "what will change and why"),
    "review_completed": ("report the review agent wrote", "is the change right, and what is risky or unverified"),
    "attempt_finished": ("final message of the implementing agent", "what it changed, or why it could not"),
}


def _plan_body(payload: Dict[str, Any]) -> str:
    lines = [str(payload.get("summary") or "")]
    lines += [f"- step: {s}" for s in payload.get("steps") or []]
    lines += [f"- criterion: {c}" for c in payload.get("acceptance_criteria") or []]
    lines += [f"- {r}" for r in payload.get("risks") or []]
    return "\n".join(lines)


def _review_body(payload: Dict[str, Any]) -> str:
    lines = [f"Verdict: {payload.get('verdict') or ''}", str(payload.get("summary") or "")]
    for f in payload.get("findings") or []:
        if isinstance(f, dict):
            lines.append(f"- {f.get('severity')}: {f.get('title')}: {f.get('detail') or ''}")
    for c in payload.get("plan_coverage") or []:
        if isinstance(c, dict) and c.get("status") != "met":
            lines.append(f"- {c.get('criterion_id')} {c.get('status')}: {c.get('evidence') or ''}")
    return "\n".join(lines)


def candidates(service, job) -> List[Dict[str, Any]]:
    """``[{key, kind, body, event}]`` for every stage outcome without an agent-written headline."""
    done = written_notes(job)
    attempts = {a.id: a for a in service.attempts(job.id)}
    out = []
    for event in service.store.list_events_by_type(job.id, tuple(_KINDS)):
        key, payload = f"e{event.id}", event.payload or {}
        if key in done:
            continue
        if event.event_type == "plan_created" and not payload.get("headline") and payload.get("summary"):
            out.append({"key": key, "kind": event.event_type, "body": _plan_body(payload), "event": event})
        elif event.event_type == "review_completed" and not payload.get("headline") and payload.get("summary"):
            out.append({"key": key, "kind": event.event_type, "body": _review_body(payload), "event": event})
        elif event.event_type == "attempt_finished" and event.reason == "completed":
            attempt = attempts.get(str(payload.get("attempt_id") or ""))
            text = str(((getattr(attempt, "metadata", None) or {}).get("agent_result") or {}).get("text") or "")
            if text.strip() and not parse_handoff(text)[0]:
                out.append({"key": key, "kind": event.event_type, "body": text, "event": event})
    return out


def write_note(job, kind: str, title: str, body: str, generate: GenerateFn) -> Dict[str, Any]:
    from .stages import extract_json
    what, hint = _KINDS[kind]
    reply = generate(_PROMPT.format(what=what, what_cap=what[0].upper() + what[1:], headline_hint=hint,
                                    title=title, body=body[:12000]))
    data = extract_json(reply)
    return {"headline": clean(data.get("headline")), "notes": bullets(data.get("notes"), limit=3)}


def _update_review(art, event, note: Dict[str, Any]) -> None:
    """The latest review.json, when it is this round's report, gains the headline."""
    review = art.read_json("review.json", None)
    if isinstance(review, dict) and review.get("summary") == (event.payload or {}).get("summary") \
            and not review.get("headline"):
        review["headline"], review["notes"] = note["headline"], note["notes"]
        art.write_json("review.json", review)


def backfill(service, generate: GenerateFn, *, dry_run: bool = False,
             log: Callable[[str], None] = print, jobs: Optional[List[Any]] = None) -> int:
    """Write missing headlines for every job; returns how many were written."""
    from .core.artifacts import artifacts_for
    written = 0
    for job in jobs if jobs is not None else service.store.list_jobs(limit=100000):
        todo = candidates(service, job)
        if not todo:
            continue
        art = artifacts_for(job).ensure()
        notes = written_notes(job)
        for item in todo:
            if dry_run:
                log(f"{job.id[:8]} {item['kind']} {item['key']}: would write")
                continue
            note = write_note(job, item["kind"], job.title, item["body"], generate)
            if not note["headline"]:
                log(f"{job.id[:8]} {item['kind']} {item['key']}: no headline returned, skipped")
                continue
            notes[item["key"]] = note
            art.write_json(WRITTEN_NOTES, notes)
            if item["kind"] == "review_completed":
                _update_review(art, item["event"], note)
            written += 1
            log(f"{job.id[:8]} {item['kind']}: {note['headline']}")
    return written


def model_generate(provider: str, model: str) -> GenerateFn:
    from mu.editor.service import make_provider
    from providers.base import Message, MessagePart
    llm = make_provider(provider, model, purpose="notes")

    def run(prompt: str) -> str:
        return llm.generate([Message(role="user", parts=[MessagePart(type="text", text=prompt)])]).text
    return run


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="list what would be written; call no model")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--job", action="append", default=[], help="only this job id (repeatable)")
    args = parser.parse_args(argv)
    from .service import JobService
    service = JobService()
    jobs = [service.get(j) for j in args.job] or None
    generate = (lambda _prompt: "{}") if args.dry_run else model_generate(args.provider, args.model)
    count = backfill(service, generate, dry_run=args.dry_run, jobs=jobs)
    print(json.dumps({"written": count, "dry_run": args.dry_run}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
