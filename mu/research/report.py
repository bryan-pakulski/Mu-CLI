"""Outputs generated from the evidence ledger.

* ``bibliography`` — cited sources grouped by root question, plus read but
  uncited sources under "Consulted"; candidates never appear. Entries keep the
  citation number the agent saw when it read the source, so ``[^n]`` in prose
  written before the report still points at the right entry.
* ``report_skeleton`` — Answer / Findings / Contested / Gaps / Method /
  Sources, built only from recorded claims, for the agent to write into.
* ``export_files`` — the deep-research ``supporting_data`` register, evidence
  log and question tree.
* ``matrix`` — claims × origins with stances, for the GUI and
  ``publish_visualization``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .urls import display_url

if TYPE_CHECKING:  # pragma: no cover
    from .sources import ResearchState, Source

_TYPE_LABEL = {
    "academic": "[Academic]",
    "social": "[Social]",
    "forum": "[Forum]",
    "news": "[News]",
    "documentation": "[Documentation]",
    "web": "[Web]",
}


def _authors(authors: List[str]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return f"{authors[0]} & {authors[1]}"
    return f"{authors[0]} et al."


def bibliography_entry(source: "Source") -> str:
    head = f"[^{source.citation_id}]: "
    if source.authors:
        head += f"{_authors(source.authors)}. "
    parts = [head + f"{source.title}."]
    if source.site:
        parts.append(f"{source.site}.")
    parts.append(display_url(source.url))
    if source.date:
        parts.append(f"Published: {source.date}.")
    parts.append(f"Accessed: {source.accessed_date}.")
    label = _TYPE_LABEL.get(source.source_type.value)
    if label:
        parts.append(label)
    if source.assessed and source.credibility_score > 0:
        stars = int(round(source.credibility_score * 5))
        parts.append(f"(Credibility: {'★' * stars}{'☆' * (5 - stars)} {source.credibility_score:.1f}/1.0)")
    else:
        parts.append("(Credibility: unassessed)")
    return " ".join(parts)


def _in_question(state: "ResearchState", source: "Source", question_id: Optional[str]) -> bool:
    if not question_id:
        return True
    return any(q == question_id or q.startswith(question_id + ".") for q in source.question_ids)


def bibliography(state: "ResearchState", include: str = "read",
                 question_id: Optional[str] = None) -> str:
    """Markdown bibliography; ``include="cited"`` omits the Consulted section."""
    numbered = [s for s in state.numbered_sources() if _in_question(state, s, question_id)]
    cited = [s for s in numbered if s.state == "cited"]
    consulted = [s for s in numbered if s.state == "read"] if include != "cited" else []
    if not cited and not consulted:
        return ""
    lines = ["## Bibliography", ""]
    groups: Dict[str, List["Source"]] = {}
    for s in cited:
        groups.setdefault(state.group_label(s), []).append(s)
    for label, items in groups.items():
        if len(groups) > 1 or label != "general":
            lines += [f"### {label}", ""]
        for s in items:
            lines += [bibliography_entry(s), ""]
    if consulted:
        lines += ["### Consulted", ""]
        for s in consulted:
            lines += [bibliography_entry(s), ""]
    return "\n".join(lines).strip()


def _cites(claim) -> str:
    seen: List[int] = []
    for ev in claim.evidence:
        if ev.stance != "contradicts" and ev.citation_id not in seen:
            seen.append(ev.citation_id)
    return "".join(f"[^{c}]" for c in seen)


def _questions_in_order(state: "ResearchState", question_id: Optional[str]):
    qs = state.questions()
    if question_id:
        qs = [q for q in qs if q.id == question_id or q.id.startswith(question_id + ".")]
    return qs


def report_skeleton(state: "ResearchState", question_id: Optional[str] = None) -> str:
    qs = _questions_in_order(state, question_id)
    lines = ["## Answer", "", "_Write the direct answer here, citing claims below._", "", "## Findings", ""]
    contested: List[str] = []
    gaps: List[str] = []
    any_claims = False
    for q in qs:
        status = state.question_status(q)
        if status == "dropped":
            continue
        claims = state.question_claims(q.id)
        if not claims and state.subtree_claims(q.id):
            continue  # answered through its sub-questions, listed below
        if not claims:
            unread = [s for s in state.all_sources("candidate") if q.id in s.question_ids][:3]
            hint = ""
            if unread:
                hint = " · unread: " + ", ".join(f"{s.candidate_id} {s.title}" for s in unread)
            gaps.append(f"- {q.text} ({q.id}){hint}")
            continue
        any_claims = True
        levels = [state.claim_confidence(c) for c in claims]
        order = {"high": 0, "medium": 1, "low": 2}
        worst = max(levels, key=lambda lvl: order[lvl])
        lines += [f"### {q.text}  (confidence: {worst} · {status})", ""]
        for c, level in zip(claims, levels):
            tag = "" if c.kind == "observed" else f" _{c.kind}_"
            lines.append(f"- {c.text} {_cites(c)} — {level}{tag}")
            for ev in c.evidence:
                loc = f" {ev.locator}" if ev.locator else ""
                lines.append(f'  - {ev.stance}: "{ev.excerpt}" — [^{ev.citation_id}]{loc}')
            if state.claim_contested(c):
                o = state.claim_origins(c)
                sup = "".join(f"[^{e.citation_id}]" for e in c.evidence if e.stance == "supports")
                con = "".join(f"[^{e.citation_id}]" for e in c.evidence if e.stance == "contradicts")
                contested.append(
                    f"- {c.text}: supports {sup} ({', '.join(o['supports'])}) vs contradicts "
                    f"{con} ({', '.join(o['contradicts'])})"
                )
        lines.append("")
    if not any_claims:
        lines += ["_No claims recorded yet: record them with record_claim._", ""]
    lines += ["## Contested", ""] + (contested or ["- none"]) + [""]
    lines += ["## Gaps", ""] + (gaps or ["- none"]) + [""]
    counts = {st: len(state.all_sources(st)) for st in ("candidate", "read", "cited")}
    found = counts["candidate"] + counts["read"] + counts["cited"]
    lines += [
        "## Method",
        "",
        f"- {found} sources found · {counts['read'] + counts['cited']} read · {counts['cited']} cited",
        f"- {len(state.claims())} claims across {len([q for q in qs if not q.dropped])} questions",
        "",
        "## Sources",
        "",
    ]
    bib = bibliography(state, include="read", question_id=question_id)
    lines.append(bib.split("\n", 2)[2] if bib.startswith("## Bibliography\n") else (bib or "- none"))
    return "\n".join(lines).rstrip() + "\n"


def _cell(text: Any) -> str:
    return " ".join(str(text if text is not None else "").split()).replace("|", "\\|")


def export_files(state: "ResearchState") -> Dict[str, str]:
    """``{filename: markdown}`` for the deep-research ``supporting_data`` folder."""
    q_lines = ["# Research questions", ""]

    def walk(parent: Optional[str], depth: int) -> None:
        for q in state.questions():
            if q.parent_id != parent:
                continue
            claims = state.question_claims(q.id)
            q_lines.append(
                f"{'  ' * depth}- **{q.id}** {q.text} — {state.question_status(q)}, "
                f"{len(claims)} claim(s)" + (f", todo #{q.todo_id}" if q.todo_id else "")
            )
            walk(q.id, depth + 1)

    walk(None, 0)
    if len(q_lines) == 2:
        q_lines.append("_No questions recorded._")

    reg = [
        "# Source register",
        "",
        "| # | Title | Type | Origin | Credibility | State | URL |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in state.numbered_sources():
        cred = f"{s.credibility_score:.2f}" if s.assessed else "unassessed"
        reg.append(
            f"| {s.citation_id} | {_cell(s.title)} | {s.source_type.value} | {_cell(s.origin)} | "
            f"{cred} | {s.state} | {_cell(display_url(s.url))} |"
        )
    if len(reg) == 4:
        reg.append("| — | No sources read | | | | | |")

    log = [
        "# Evidence log",
        "",
        "| Claim | Question | Kind | Confidence | Stance | Source | Excerpt | Locator |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in state.claims():
        level = state.claim_confidence(c)
        for ev in c.evidence:
            log.append(
                f"| {c.id} {_cell(c.text)} | {c.question_id} | {c.kind} | {level} | {ev.stance} | "
                f"[^{ev.citation_id}] | {_cell(ev.excerpt)} | {_cell(ev.locator or '')} |"
            )
    if len(log) == 4:
        log.append("| — | | | | | | No claims recorded | |")

    return {
        "questions.md": "\n".join(q_lines) + "\n",
        "source_register.md": "\n".join(reg) + "\n",
        "evidence_log.md": "\n".join(log) + "\n",
    }


def matrix(state: "ResearchState", question_id: Optional[str] = None) -> Dict[str, Any]:
    """Claims × origins. Each cell is the list of stances that origin takes."""
    claims = state.claims()
    if question_id:
        claims = [c for c in claims
                  if c.question_id == question_id or c.question_id.startswith(question_id + ".")]
    origins: List[str] = []
    rows = []
    for c in claims:
        cells: Dict[str, List[str]] = {}
        for ev in c.evidence:
            src = state.get_source(ev.citation_id)
            if src is None:
                continue
            if src.origin not in origins:
                origins.append(src.origin)
            stances = cells.setdefault(src.origin, [])
            if ev.stance not in stances:
                stances.append(ev.stance)
        rows.append({"claim_id": c.id, "question_id": c.question_id, "text": c.text,
                     "confidence": state.claim_confidence(c), "cells": cells})
    return {"origins": origins, "rows": rows}
