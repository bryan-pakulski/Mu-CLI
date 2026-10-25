"""Debug-mode tools over the debug case engine (``mu.debug.engine``)."""

from __future__ import annotations

from typing import Any, Dict

from mu.debug import engine
from mu.debug.state import HYPOTHESIS_STATUSES, debug_state
from mu.modes.preview import register_preview
from mu.modes.tooling import envelope, guarded, owner, run_line
from mu.tools import tool

_EVIDENCE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["run", "location", "search", "note"]},
            "ref": {"type": "string", "description": "r<n> for a run, path:line for a location, the query for a search."},
            "stance": {"type": "string", "enum": ["supports", "refutes"]},
            "note": {"type": "string"},
        },
        "required": ["kind", "stance"],
    },
}


def _case_data(case) -> Dict[str, Any]:
    data = case.to_dict()
    data["hypotheses"] = [{"id": h["id"], "statement": h["statement"], "status": h["status"],
                           "evidence": len(h["evidence"])} for h in data["hypotheses"]]
    return data


@tool(
    name="open_debug_case",
    description="Open (and select) a debug case for one bug: a short title and the observed symptom.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short name, e.g. 'KeyError in sync'."},
            "symptom": {"type": "string", "description": "What fails and how, e.g. the failing command and error."},
        },
        "required": ["title", "symptom"],
    },
    requires_approval=False, phase="debug", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("open_debug_case")
def open_debug_case(args: Dict[str, Any], context) -> Dict[str, Any]:
    case = engine.open_case(owner(context), args.get("title"), args.get("symptom"))
    return envelope("open_debug_case", True,
                    f"Case {case.id} open: {case.title}. Next: record_repro with the failing command.",
                    _case_data(case))


@tool(
    name="record_repro",
    description=(
        "Run the reproducer for the current case. Accepted only if it FAILS as described: "
        "the expect_marker appears in the output, or (no marker) the command exits non-zero. "
        "Re-run to narrow it down."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Minimal failing command, e.g. pytest path::test -x."},
            "expect_marker": {"type": "string", "description": "Text the failure prints (e.g. \"KeyError: 'id'\"). Omit to use a non-zero exit."},
            "timeout_seconds": {"type": "integer", "default": 300},
        },
        "required": ["command"],
    },
    requires_approval=True, phase="debug", execution_kind="mutate", preview_policy="optional",
    server_policy="session_only",
)
@guarded("record_repro")
def record_repro(args: Dict[str, Any], context) -> Dict[str, Any]:
    case, run = engine.record_repro(owner(context), str(args.get("command") or ""),
                                    expect_marker=str(args.get("expect_marker") or ""),
                                    timeout=int(args.get("timeout_seconds") or 300))
    data = {"case": case.id, "status": case.status, "run": run.brief()}
    if run.passed:
        return envelope("record_repro", True,
                        f"Reproduced ({run_line(run)}). Case {case.id} is reproduced.", data)
    return envelope("record_repro", False,
                    f"Not accepted: the command did not fail as described ({run.detail}). "
                    "Narrow the command or fix expect_marker.", data, "repro_not_failing")


@tool(
    name="add_hypothesis",
    description="Add a root-cause hypothesis to the current case (status starts untested).",
    parameters={"type": "object", "properties": {"statement": {"type": "string"}}, "required": ["statement"]},
    requires_approval=False, phase="debug", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("add_hypothesis")
def add_hypothesis(args: Dict[str, Any], context) -> Dict[str, Any]:
    hyp = engine.add_hypothesis(owner(context), args.get("statement"))
    return envelope("add_hypothesis", True, f"{hyp.id} added: {hyp.statement}",
                    {"id": hyp.id, "status": hyp.status})


@tool(
    name="update_hypothesis",
    description=(
        "Set a hypothesis' status with evidence. 'confirmed' needs a supporting check run (r<n>) "
        "or code location (path:line); 'refuted' needs refuting evidence."
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Hypothesis id, e.g. h1."},
            "status": {"type": "string", "enum": list(HYPOTHESIS_STATUSES)},
            "evidence": _EVIDENCE_SCHEMA,
        },
        "required": ["id", "status"],
    },
    requires_approval=False, phase="debug", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("update_hypothesis")
def update_hypothesis(args: Dict[str, Any], context) -> Dict[str, Any]:
    hyp = engine.update_hypothesis(owner(context), str(args.get("id") or ""), args.get("status"),
                                   evidence=args.get("evidence") or [])
    case = engine.require_case(owner(context))
    return envelope("update_hypothesis", True, f"{hyp.id} is {hyp.status}; case {case.id} is {case.status}.",
                    {"id": hyp.id, "status": hyp.status, "case_status": case.status,
                     "evidence": hyp.evidence})


@tool(
    name="add_suspect",
    description="Record a suspect code location for the current case (the path must exist).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "line": {"type": "integer"},
            "symbol": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["path", "reason"],
    },
    requires_approval=False, phase="debug", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("add_suspect")
def add_suspect(args: Dict[str, Any], context) -> Dict[str, Any]:
    line = args.get("line")
    suspect = engine.add_suspect(owner(context), str(args.get("path") or ""), args.get("reason"),
                                 line=int(line) if line else None, symbol=str(args.get("symbol") or ""))
    where = f"{suspect.path}:{suspect.line}" if suspect.line else suspect.path
    return envelope("add_suspect", True, f"Suspect recorded: {where}", {"path": suspect.path, "line": suspect.line})


@tool(
    name="verify_fix",
    description=(
        "After fixing: re-runs the recorded repro (must now pass), then suite_command (the wider "
        "suite, must pass), then optionally the repro `repeat` times for flaky bugs. The case is "
        "verified only if all pass."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "What the fix changed and why it addresses the root cause."},
            "suite_command": {"type": "string", "description": "The wider run: the whole test file or suite."},
            "files": {"type": "array", "items": {"type": "string"}, "description": "Files changed (default: git status)."},
            "repeat": {"type": "integer", "description": "Re-run the repro this many times (flakes/races)."},
            "timeout_seconds": {"type": "integer", "default": 600},
        },
        "required": ["summary", "suite_command"],
    },
    requires_approval=True, phase="debug", execution_kind="mutate", preview_policy="optional",
    server_policy="session_only",
)
@guarded("verify_fix")
def verify_fix(args: Dict[str, Any], context) -> Dict[str, Any]:
    result = engine.verify_fix(owner(context), summary=str(args.get("summary") or ""),
                               suite_command=str(args.get("suite_command") or ""),
                               files=args.get("files") or None, repeat=int(args.get("repeat") or 0),
                               timeout=int(args.get("timeout_seconds") or 600))
    case = result["case"]
    lines = [run_line(result["after"]), run_line(result["suite"])]
    if result["repeats"]:
        passed = sum(1 for r in result["repeats"] if r.passed)
        lines.append(f"repeats: {passed}/{len(result['repeats'])} passed")
    data = {"case": case.id, "status": case.status, "verified": result["verified"],
            "repro_after": result["after"].brief(), "suite": result["suite"].brief(),
            "repeats": [r.id for r in result["repeats"]]}
    if result["verified"]:
        return envelope("verify_fix", True, f"Verified. {'; '.join(lines)}", data)
    return envelope("verify_fix", False, f"Not verified. {'; '.join(lines)}", data, "verification_failed")


@tool(
    name="record_regression_test",
    description=(
        "Record a regression test for the current case. The engine shows it fails on the pre-fix "
        "tree (a git worktree of HEAD with your new test copied in) and passes now."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Test file path."},
            "name": {"type": "string", "description": "Test name."},
            "command": {"type": "string", "description": "Command that runs just this test (default for .py: pytest path::name)."},
        },
        "required": ["path", "name"],
    },
    requires_approval=True, phase="debug", execution_kind="mutate", preview_policy="optional",
    server_policy="session_only",
)
@guarded("record_regression_test")
def record_regression_test(args: Dict[str, Any], context) -> Dict[str, Any]:
    result = engine.record_regression_test(owner(context), path=str(args.get("path") or ""),
                                           name=str(args.get("name") or ""),
                                           command=str(args.get("command") or ""))
    data = {"case": result["case"].id, "recorded": result["recorded"],
            "failed_before": result["failed_before"], "passes_after": result["passes_after"].brief()}
    if result["recorded"]:
        return envelope("record_regression_test", True,
                        f"Regression test recorded: fails before ({result['failed_before']}), passes now "
                        f"({result['passes_after'].id}).", data)
    return envelope("record_regression_test", False, f"Not recorded: {result['reason']}.", data,
                    "regression_not_shown")


@tool(
    name="close_debug_case",
    description=(
        "Close the current case. Needs a verified fix (verify_fix) and the root cause, which is "
        "saved to durable memory; or an unreproducible_reason."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cause": {"type": "string", "description": "The actual defect (not the symptom)."},
            "signature": {"type": "string", "description": "How the bug shows itself (error text, symptom) for future recall."},
            "fix_summary": {"type": "string"},
            "unreproducible_reason": {"type": "string", "description": "Close as unreproducible instead, with why."},
        },
    },
    requires_approval=False, phase="debug", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("close_debug_case")
def close_debug_case(args: Dict[str, Any], context) -> Dict[str, Any]:
    result = engine.close_case(owner(context), cause=str(args.get("cause") or ""),
                               signature=str(args.get("signature") or ""),
                               fix_summary=str(args.get("fix_summary") or ""),
                               unreproducible_reason=str(args.get("unreproducible_reason") or ""))
    case = result["case"]
    message = f"Case {case.id} {case.status}."
    if result["memory_id"]:
        message += " Root cause saved to memory."
    if result["gaps"]:
        message += " Gap: " + "; ".join(result["gaps"]) + "."
    return envelope("close_debug_case", True, message,
                    {"case": case.id, "status": case.status, "memory_id": result["memory_id"],
                     "gaps": result["gaps"]})


@tool(
    name="debug_status",
    description="Show the current debug case (or list cases with all=true), with runs and next step.",
    parameters={
        "type": "object",
        "properties": {
            "all": {"type": "boolean", "description": "List every case."},
            "select": {"type": "string", "description": "Make this case id current."},
        },
    },
    requires_approval=False, phase="debug", execution_kind="read", preview_policy="none",
    server_policy="session_only",
)
@guarded("debug_status")
def debug_status(args: Dict[str, Any], context) -> Dict[str, Any]:
    own = owner(context)
    if args.get("select"):
        engine.select_case(own, str(args["select"]))
    state = debug_state(own)
    if args.get("all"):
        cases = [engine.case_summary(c) for c in state.cases.values()]
        lines = [f"{c['id']} [{c['status']}] {c['title']}" for c in cases] or ["No cases."]
        return envelope("debug_status", True, "\n".join(lines), {"cases": cases, "current": state.current})
    case = engine.require_case(own)
    session = getattr(context, "session", None)
    text = engine.capsule(session) if session is not None else case.title
    return envelope("debug_status", True, text, _case_data(case))


@tool(
    name="bisect_repro",
    description=(
        "Find the commit that introduced the bug: git bisect between `good` and `bad` with the "
        "recorded repro as the test, in a separate worktree (your tree is untouched)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "good": {"type": "string", "description": "A revision where the repro passes."},
            "bad": {"type": "string", "description": "A revision where it fails (default HEAD)."},
        },
        "required": ["good"],
    },
    requires_approval=True, phase="debug", execution_kind="mutate", preview_policy="optional",
    server_policy="session_only",
)
@guarded("bisect_repro")
def bisect_repro(args: Dict[str, Any], context) -> Dict[str, Any]:
    result = engine.bisect_repro(owner(context), good=str(args.get("good") or ""),
                                 bad=str(args.get("bad") or "HEAD"))
    if result["first_bad"]:
        return envelope("bisect_repro", True,
                        f"First bad commit: {result['first_bad']} ({len(result['steps'])} steps).", result)
    return envelope("bisect_repro", False, "Bisect was inconclusive; see the run output.", result,
                    "bisect_inconclusive")


# ------------------------------------------------------------------ approval previews


def _current_case(session):
    states = getattr(getattr(session, "session_manager", None), "mode_state", None)
    state = states.peek("debug") if states is not None else None
    return state.current_case() if state is not None else None


@register_preview("verify_fix")
def _preview_verify(args, session):
    case = _current_case(session)
    return {"will_run": [c for c in [(case.repro.get("command") if case else ""),
                                     args.get("suite_command")] if c]}


@register_preview("bisect_repro")
def _preview_bisect(args, session):
    case = _current_case(session)
    return {"will_run": f"git bisect (worktree) with: {case.repro.get('command', '') if case else ''}"}


@register_preview("record_regression_test")
def _preview_regression(args, session):
    path, name = str(args.get("path") or ""), str(args.get("name") or "")
    command = args.get("command") or (f"python3 -m pytest -q -p no:cacheprovider {path}::{name}"
                                      if path.endswith(".py") else "")
    return {"will_run": f"{command} (on HEAD in a worktree, then on your tree)"}


__all__ = ["open_debug_case", "record_repro", "add_hypothesis", "update_hypothesis", "add_suspect",
           "verify_fix", "record_regression_test", "close_debug_case", "debug_status", "bisect_repro"]
