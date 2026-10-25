"""Evidence-ledger tools: questions, claims, evidence, status, report, export.

The ledger tools change only the session's ``ResearchState`` (no filesystem
or network), so they run without an approval prompt like ``assess_source``.
``research_export`` writes files and goes through the normal approval-gated
write path with workspace bounds (and is blocked in plan mode).
They belong to the ``research`` tool phase, so only research mode sees them.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from mu.research import ResearchError
from mu.research.report import export_files, matrix, report_skeleton
from mu.tools import tool
from mu.tools.capabilities import session_type_from_context

from .handlers import _engine

_EVIDENCE_ITEM = {
    "type": "object",
    "properties": {
        "citation_id": {"type": "integer", "description": "Citation number [^n] of a source you have read."},
        "stance": {"type": "string", "enum": ["supports", "contradicts", "qualifies"]},
        "excerpt": {"type": "string", "description": "Exact quote from the source text (≤ 500 chars)."},
        "locator": {"type": "string", "description": "Section heading, page ('Page 3') or anchor."},
    },
    "required": ["citation_id", "stance", "excerpt"],
}


def _fail(exc: Exception) -> Dict[str, Any]:
    return {"ok": False, "error": str(exc)}


def _claim_view(state, claim) -> Dict[str, Any]:
    return {
        "claim_id": claim.id,
        "question_id": claim.question_id,
        "confidence": state.claim_confidence(claim),
        "contested": state.claim_contested(claim),
        "origins": state.claim_origins(claim),
        "evidence": len(claim.evidence),
    }


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@tool(
    name="research_question",
    description=(
        "Build the research question tree. action=add creates a question (parent_id makes "
        "it a sub-question) and selects it so sources you read are filed under it; select "
        "switches the current question; update edits text/todo link; drop sets it aside. "
        "Plan questions before searching."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "select", "update", "drop"]},
            "text": {"type": "string", "description": "Question text (add/update)."},
            "id": {"type": "string", "description": "Question id such as 'q1' or 'q1.2' (select/update/drop)."},
            "parent_id": {"type": "string", "description": "Parent question id for a sub-question (add)."},
            "todo_id": {"type": "integer", "description": "Optional linked todo item id."},
        },
        "required": ["action"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured",
)
def _research_question_tool(args: Dict[str, Any], context) -> Any:
    state = _engine(context)
    action = str(args.get("action") or "add").strip().lower()
    try:
        if action == "add":
            q = state.add_question(str(args.get("text") or ""), parent_id=args.get("parent_id") or None,
                                   todo_id=_as_int(args.get("todo_id")))
        elif action == "select":
            q = state.update_question(str(args.get("id") or ""), select=True)
        elif action == "update":
            q = state.update_question(str(args.get("id") or ""), text=args.get("text"),
                                      todo_id=_as_int(args.get("todo_id")))
        elif action == "drop":
            q = state.update_question(str(args.get("id") or ""), dropped=True)
        else:
            raise ResearchError("action must be add, select, update or drop")
    except ResearchError as exc:
        return _fail(exc)
    current = state.current_question()
    return json.dumps({"question_id": q.id, "text": q.text, "parent_id": q.parent_id,
                       "status": state.question_status(q),
                       "current_question": current.id if current else None})


@tool(
    name="record_claim",
    description=(
        "Record a finding for a question, backed by evidence quoted from sources you have "
        "READ (citation numbers from url_grounding / research_fetch / read_document). Each "
        "excerpt must occur in the source text. kind: observed (stated by the source), "
        "derived (computed from sources), inferred (your judgement; capped at medium). "
        "Confidence is computed from independent origins; you may only lower it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question_id": {"type": "string"},
            "text": {"type": "string", "description": "The claim, one sentence."},
            "kind": {"type": "string", "enum": ["observed", "derived", "inferred"], "default": "observed"},
            "evidence": {"type": "array", "items": _EVIDENCE_ITEM},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"],
                           "description": "Optional cap below the computed confidence."},
        },
        "required": ["question_id", "text", "evidence"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured",
)
def _record_claim_tool(args: Dict[str, Any], context) -> Any:
    state = _engine(context)
    evidence = args.get("evidence")
    if isinstance(evidence, dict):
        evidence = [evidence]
    try:
        claim = state.record_claim(
            str(args.get("question_id") or ""), str(args.get("text") or ""),
            str(args.get("kind") or "observed"), list(evidence or []),
            confidence=args.get("confidence") or None,
        )
    except ResearchError as exc:
        return _fail(exc)
    return json.dumps(_claim_view(state, claim))


@tool(
    name="add_evidence",
    description=(
        "Attach more evidence to a recorded claim — another supporting source, a "
        "contradiction, or a qualification. Same rules as record_claim: the source must be "
        "read and the excerpt must occur in its text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "claim_id": {"type": "string"},
            **_EVIDENCE_ITEM["properties"],
        },
        "required": ["claim_id", "citation_id", "stance", "excerpt"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured",
)
def _add_evidence_tool(args: Dict[str, Any], context) -> Any:
    state = _engine(context)
    item = {k: args.get(k) for k in ("citation_id", "stance", "excerpt", "locator")}
    try:
        claim = state.add_evidence(str(args.get("claim_id") or ""), item)
    except ResearchError as exc:
        return _fail(exc)
    return json.dumps(_claim_view(state, claim))


def status_text(state, coverage: Dict[str, Any]) -> str:
    counts = coverage["sources"]
    lines = [
        f"Sources: {counts['candidate']} candidate · {counts['read']} read · {counts['cited']} cited · "
        f"{coverage['claims']} claim(s)"
    ]
    current = coverage.get("current_question")
    if not coverage["questions"]:
        lines.append("No questions yet — add them with research_question(action='add').")
    for row in coverage["questions"]:
        depth = row["id"].count(".")
        mark = " ← current" if row["id"] == current else ""
        conf = row["confidence"]
        lines.append(
            f"{'  ' * depth}- {row['id']} [{row['status']}] {row['text']}{mark}\n"
            f"{'  ' * depth}  {row['claims']} claim(s) (high {conf['high']}, medium {conf['medium']}, "
            f"low {conf['low']}) · {row['origins']} supporting origin(s) · "
            f"{row['contradictions']} contradiction(s)"
        )
        for cand in row["unread_candidates"][:3]:
            lines.append(f"{'  ' * depth}  unread: {cand['id']} {cand['title']}")
    if coverage["open_without_claims"]:
        lines.append("Gaps (no claims yet): " + ", ".join(coverage["open_without_claims"]))
    return "\n".join(lines)


@tool(
    name="research_status",
    description=(
        "Coverage of the research so far: per question its status, claims, confidence, "
        "independent origins, contradictions and unread candidates worth reading, plus "
        "questions with no claims yet. visual=true adds a claims × origins matrix suitable "
        "for publish_visualization."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question_id": {"type": "string", "description": "Limit to one question and its sub-questions."},
            "visual": {"type": "boolean", "default": False},
        },
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured",
)
def _research_status_tool(args: Dict[str, Any], context) -> Any:
    state = _engine(context)
    qid = str(args.get("question_id") or "").strip() or None
    coverage = state.coverage(qid)
    data: Dict[str, Any] = {"coverage": coverage}
    if args.get("visual"):
        data["matrix"] = matrix(state, qid)
    return {"ok": True, "message": status_text(state, coverage), "data": data}


@tool(
    name="research_report",
    description=(
        "Report skeleton built from the evidence ledger: Answer (for you to write), Findings "
        "per question with confidence and quoted evidence, Contested, Gaps, Method and the "
        "Sources bibliography. Only recorded claims appear — record them first."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question_id": {"type": "string", "description": "Limit to one question and its sub-questions."},
        },
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured",
)
def _research_report_tool(args: Dict[str, Any], context) -> str:
    qid = str(args.get("question_id") or "").strip() or None
    return report_skeleton(_engine(context), qid)


def research_export(directory: str, context) -> str:
    from mu.tools.file.handlers import write_file

    directory = str(directory or "").strip() or "supporting_data"
    folder_context = getattr(context, "folder_context", None)
    session_type = session_type_from_context(context)
    written: List[str] = []
    for name, body in export_files(_engine(context)).items():
        result = write_file(os.path.join(directory, name), body, folder_context, session_type=session_type)
        if not str(result).startswith("Successfully"):
            return f"Error exporting {name}: {result}" + (
                f" (already written: {', '.join(written)})" if written else ""
            )
        written.append(name)
    return f"Exported {', '.join(written)} to {directory}"


@tool(
    name="research_export",
    description=(
        "Write the deep-research supporting files generated from the ledger — "
        "source_register.md, evidence_log.md and questions.md — into a workspace directory "
        "(default 'supporting_data')."
    ),
    parameters={
        "type": "object",
        "properties": {
            "dir": {"type": "string", "description": "Target directory inside the workspace."},
        },
        "required": ["dir"],
    },
    requires_approval=True,
    phase="research",
    execution_kind="mutate",
    preview_policy="none",
)
def _research_export_tool(args: Dict[str, Any], context) -> str:
    return research_export(args.get("dir", ""), context)
