"""Debug case engine (modes v2 §4.2).

A case climbs ``open → reproduced → root_caused → fixed → verified`` and ends
``closed`` (or ``unreproducible``). Each rung is earned by an engine-run check:

* ``record_repro`` runs the command (``run_check``); it is accepted only when
  it FAILS as described (a marker in the output, or a non-zero exit).
* a hypothesis is ``confirmed`` only with a supporting run or code location.
* ``verify_fix`` re-runs the repro (it must now pass), the wider suite (must
  pass) and, optionally, the repro N times for flakes.
* ``record_regression_test`` shows the test fails on the pre-fix tree (a git
  worktree of HEAD with the new test copied in) and passes now.
* ``close_case`` needs ``verified`` (or an unreproducible reason) and promotes
  the structured root cause to the Memory Ledger.

Commands go through ``run_check``, so they are approval-gated, refused in
plan mode and checked by the secret guard. While a case has no reproducer, the
first edit of a turn is refused with a warning (``debug_require_repro`` makes
it a block); ``edit_gate`` is the pre-tool hook.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from mu.modes.events import AGENT, GateError, normalize_actor
from mu.modes.evidence import CheckSpec, checks_state, describe_predicate, run_check, workspace_root
from mu.modes.state import now

from .state import (EVIDENCE_KINDS, HYPOTHESIS_STATUSES, LADDER, STANCES, DebugCase, Hypothesis,
                    Suspect, debug_state)

EDIT_TOOLS = frozenset({"write_file", "apply_diff", "search_and_replace_file"})
RUN_TOOLS = ("record_repro", "verify_fix", "record_regression_test", "bisect_repro")


def _session(owner: Any) -> Any:
    session = getattr(owner, "session", None)
    return session if session is not None else owner


def _promote(case: DebugCase, status: str) -> None:
    if status in LADDER and case.status in LADDER and LADDER.index(status) > case.rung:
        case.status = status


def require_case(owner: Any, case_id: str = "") -> DebugCase:
    state = debug_state(owner)
    case = state.cases.get(str(case_id or "").strip()) if case_id else state.current_case()
    if case is None:
        raise GateError("No open debug case — call open_debug_case(title, symptom) first."
                        if not case_id else f"No debug case {case_id!r}.", code="not_found")
    return case


def _require_active(case: DebugCase) -> None:
    if case.status in ("closed", "unreproducible"):
        raise GateError(f"Case {case.id} is {case.status}; open a new case.", code="case_closed")


def _touch(owner: Any, case: DebugCase) -> None:
    case.updated_at = now()
    debug_state(owner).touch()


# ------------------------------------------------------------------ case lifecycle


def open_case(owner: Any, title: str, symptom: str, *, actor: str = AGENT) -> DebugCase:
    title, symptom = str(title or "").strip(), str(symptom or "").strip()
    if not title or not symptom:
        raise ValueError("A debug case needs a title and the observed symptom.")
    state = debug_state(owner)
    with state.lock:
        state.counter += 1
        case = DebugCase(id=f"d{state.counter}", title=title, symptom=symptom)
        state.cases[case.id] = case
        state.current = case.id
        state.event("opened", normalize_actor(actor), case.id, title)
        state.touch()
        return case


def select_case(owner: Any, case_id: str, *, actor: str = AGENT) -> DebugCase:
    state = debug_state(owner)
    case = require_case(owner, case_id)
    with state.lock:
        state.current = case.id
        state.event("selected", normalize_actor(actor), case.id)
        state.touch()
    return case


def failure_predicate(expect_marker: str = "") -> Dict[str, Any]:
    marker = str(expect_marker or "").strip()
    return {"markers": [marker]} if marker else {"exit_nonzero": True}


def fixed_predicate(failure: Dict[str, Any]) -> Dict[str, Any]:
    if failure.get("markers"):
        return {"all": [{"markers": list(failure["markers"]), "absent": True}, {"exit": 0}]}
    return {"exit": 0}


def record_repro(owner: Any, command: str, *, expect_marker: str = "", timeout: int = 300,
                 actor: str = AGENT) -> Tuple[DebugCase, Any]:
    """Run the reproducer; accept it only when it fails as described."""
    case = require_case(owner)
    _require_active(case)
    if case.rung >= LADDER.index("fixed"):
        raise GateError(f"Case {case.id} already has a fix; verify it with verify_fix.",
                        code="wrong_status")
    predicate = failure_predicate(expect_marker)
    run = run_check(CheckSpec(command=command, purpose="repro", predicate=predicate,
                              timeout_s=timeout, ref=case.id,
                              label="Repro fails as expected"), owner)
    state = debug_state(owner)
    with state.lock:
        runs = list(case.repro.get("runs") or [])
        runs.append(run.id)
        if run.passed:
            case.repro = {"command": command, "predicate": predicate,
                          "expect": describe_predicate(predicate), "runs": runs,
                          "accepted_run": run.id}
            _promote(case, "reproduced")
            state.event("reproduced", normalize_actor(actor), case.id, command[:200], run=run.id)
        else:
            case.repro = {**case.repro, "runs": runs}
            state.event("repro_rejected", normalize_actor(actor), case.id, run.detail, run=run.id)
        _touch(owner, case)
    return case, run


def add_hypothesis(owner: Any, statement: str, *, actor: str = AGENT) -> Hypothesis:
    case = require_case(owner)
    _require_active(case)
    statement = str(statement or "").strip()
    if not statement:
        raise ValueError("A hypothesis needs a statement.")
    state = debug_state(owner)
    with state.lock:
        number = 1 + max([int(h.id[1:]) for h in case.hypotheses if h.id[1:].isdigit()] or [0])
        hyp = Hypothesis(id=f"h{number}", statement=statement)
        case.hypotheses.append(hyp)
        state.event("hypothesis", normalize_actor(actor), f"{case.id}/{hyp.id}", statement[:200])
        _touch(owner, case)
        return hyp


def _validate_location(owner: Any, ref: str) -> str:
    match = re.fullmatch(r"(.+?)(?::(\d+))?", str(ref or "").strip())
    path = match.group(1) if match else ""
    root = workspace_root(owner)
    full = os.path.normpath(os.path.join(root, path))
    if not path or not os.path.isfile(full):
        raise ValueError(f"location {ref!r} is not a file in the workspace (use path:line)")
    if match and match.group(2):
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            count = sum(1 for _ in fh)
        if not 1 <= int(match.group(2)) <= count:
            raise ValueError(f"{path} has {count} lines; {ref!r} is out of range")
    return str(ref).strip()


def _normalize_evidence(owner: Any, evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    checks = checks_state(owner)
    for raw in evidence or []:
        if not isinstance(raw, dict):
            raise ValueError("evidence items are objects: {kind, ref, stance, note?}")
        kind = str(raw.get("kind") or "").strip().lower()
        stance = str(raw.get("stance") or "supports").strip().lower()
        ref = str(raw.get("ref") or "").strip()
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"evidence kind must be one of {', '.join(EVIDENCE_KINDS)}")
        if stance not in STANCES:
            raise ValueError("evidence stance must be supports or refutes")
        if kind == "run" and checks.get(ref) is None:
            raise ValueError(f"no check run {ref!r} (runs are r<n> from record_repro / verify_fix)")
        if kind == "location":
            ref = _validate_location(owner, ref)
        if kind in ("search", "note") and not (ref or str(raw.get("note") or "").strip()):
            raise ValueError(f"{kind} evidence needs a ref or a note")
        out.append({"kind": kind, "ref": ref, "stance": stance,
                    "note": str(raw.get("note") or "").strip()[:500]})
    return out


def update_hypothesis(owner: Any, hyp_id: str, status: str, *,
                      evidence: Optional[List[Dict[str, Any]]] = None,
                      actor: str = AGENT) -> Hypothesis:
    case = require_case(owner)
    _require_active(case)
    hyp = case.hypothesis(hyp_id)
    if hyp is None:
        raise ValueError(f"No hypothesis {hyp_id!r} in case {case.id}.")
    status = str(status or "").strip().lower()
    if status not in HYPOTHESIS_STATUSES:
        raise ValueError(f"status must be one of {', '.join(HYPOTHESIS_STATUSES)}")
    items = _normalize_evidence(owner, evidence or [])
    combined = hyp.evidence + items
    if status == "confirmed" and not any(
            e["stance"] == "supports" and e["kind"] in ("run", "location") for e in combined):
        raise GateError("Confirming a hypothesis needs supporting evidence from a check run "
                        "(r<n>) or a code location (path:line).", code="evidence_required")
    if status == "refuted" and not any(e["stance"] == "refutes" for e in combined):
        raise GateError("Refuting a hypothesis needs refuting evidence (a run, location, search "
                        "or note with stance 'refutes').", code="evidence_required")
    state = debug_state(owner)
    with state.lock:
        hyp.evidence = combined
        hyp.status = status
        hyp.updated_at = now()
        if status == "confirmed" and case.rung >= LADDER.index("reproduced"):
            _promote(case, "root_caused")
        state.event("hypothesis_status", normalize_actor(actor), f"{case.id}/{hyp.id}", status)
        _touch(owner, case)
    return hyp


def add_suspect(owner: Any, path: str, reason: str, *, line: Optional[int] = None,
                symbol: str = "", actor: str = AGENT) -> Suspect:
    case = require_case(owner)
    _require_active(case)
    ref = f"{path}:{line}" if line else str(path or "")
    _validate_location(owner, ref)
    if not str(reason or "").strip():
        raise ValueError("Say why this location is suspect.")
    rel = os.path.relpath(os.path.normpath(os.path.join(workspace_root(owner), path)),
                          workspace_root(owner))
    suspect = Suspect(path=rel, reason=str(reason).strip(), line=int(line) if line else None,
                      symbol=str(symbol or "").strip())
    state = debug_state(owner)
    with state.lock:
        case.suspects = [s for s in case.suspects if (s.path, s.line) != (suspect.path, suspect.line)]
        case.suspects.append(suspect)
        state.event("suspect", normalize_actor(actor), case.id, ref)
        _touch(owner, case)
    return suspect


def _git(root: str, *args: str) -> Tuple[int, str]:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def changed_files(owner: Any) -> List[str]:
    root = workspace_root(owner)
    code, out = _git(root, "status", "--porcelain")
    if code:
        return []
    return [line[3:].strip() for line in out.splitlines() if len(line) > 3]


def verify_fix(owner: Any, *, summary: str, suite_command: str, files: Optional[List[str]] = None,
               repeat: int = 0, timeout: int = 600, actor: str = AGENT) -> Dict[str, Any]:
    case = require_case(owner)
    _require_active(case)
    if not case.repro.get("command"):
        raise GateError(f"Case {case.id} has no accepted reproducer; record_repro first.",
                        code="repro_required")
    if not str(summary or "").strip():
        raise ValueError("Summarise the fix in one or two sentences.")
    if not str(suite_command or "").strip():
        raise ValueError("suite_command is required: the wider test run (whole test file or suite).")
    repeat = max(0, min(int(repeat or 0), 50))
    state = debug_state(owner)
    with state.lock:
        case.fix = {"summary": str(summary).strip(),
                    "files": [str(f) for f in files or []] or changed_files(owner), "at": now()}
        _promote(case, "fixed")
        _touch(owner, case)
    after_predicate = fixed_predicate(case.repro["predicate"])
    after = run_check(CheckSpec(command=case.repro["command"], purpose="verify",
                                predicate=after_predicate, timeout_s=timeout, ref=case.id,
                                label="Repro passes after the fix"), owner)
    suite = run_check(CheckSpec(command=suite_command, purpose="suite", predicate={"exit": 0},
                                timeout_s=timeout, ref=case.id, label="Wider suite"), owner)
    repeats: List[Any] = []
    for _ in range(repeat if after.passed else 0):
        repeats.append(run_check(CheckSpec(command=case.repro["command"], purpose="verify",
                                           predicate=after_predicate, timeout_s=timeout,
                                           ref=case.id, label="Repeat"), owner))
    passed_repeats = sum(1 for r in repeats if r.passed)
    ok = after.passed and suite.passed and passed_repeats == len(repeats)
    with state.lock:
        case.verification = {"repro_after": after.id, "suite": suite.id,
                             "suite_command": suite_command,
                             "repeats": {"n": len(repeats), "passed": passed_repeats,
                                         "runs": [r.id for r in repeats]},
                             "passed": ok, "at": now()}
        if ok:
            _promote(case, "verified")
        state.event("verify_fix", normalize_actor(actor), case.id,
                    "verified" if ok else "verification failed")
        _touch(owner, case)
    return {"case": case, "after": after, "suite": suite, "repeats": repeats, "verified": ok}


def _repo_root(root: str) -> Optional[str]:
    code, out = _git(root, "rev-parse", "--show-toplevel")
    return out.strip().splitlines()[0] if code == 0 and out.strip() else None


def record_regression_test(owner: Any, *, path: str, name: str, command: str = "",
                           timeout: int = 600, actor: str = AGENT) -> Dict[str, Any]:
    case = require_case(owner)
    _require_active(case)
    root = workspace_root(owner)
    full = os.path.normpath(os.path.join(root, path))
    if not os.path.isfile(full):
        raise ValueError(f"test file {path!r} does not exist")
    rel = os.path.relpath(full, root)
    if not str(name or "").strip():
        raise ValueError("name the test (function / case name)")
    command = str(command or "").strip() or (
        f"python3 -m pytest -q -p no:cacheprovider {rel}::{name}" if rel.endswith(".py") else "")
    if not command:
        raise ValueError("command is required for non-Python tests")
    now_run = run_check(CheckSpec(command=command, purpose="regression", predicate={"exit": 0},
                                  timeout_s=timeout, ref=case.id, label="Passes now"), owner)
    before_id = ""
    repo = _repo_root(root)
    if repo:
        tmp = tempfile.mkdtemp(prefix="mucli-pre-fix-")
        tree = os.path.join(tmp, "tree")
        code, out = _git(repo, "worktree", "add", "--detach", tree, "HEAD")
        if code:
            shutil.rmtree(tmp, ignore_errors=True)
            raise GateError(f"could not create a pre-fix worktree: {out.strip()[:300]}",
                            code="git_failed")
        try:
            sub = os.path.relpath(root, repo)
            target_root = os.path.normpath(os.path.join(tree, sub))
            os.makedirs(os.path.dirname(os.path.join(target_root, rel)), exist_ok=True)
            shutil.copy2(full, os.path.join(target_root, rel))
            before = run_check(CheckSpec(command=command, purpose="regression",
                                         predicate={"exit_nonzero": True}, cwd=target_root,
                                         timeout_s=timeout, ref=case.id,
                                         label="Fails on the pre-fix tree"), owner)
        finally:
            _git(repo, "worktree", "remove", "--force", tree)
            shutil.rmtree(tmp, ignore_errors=True)
        if before.passed:
            before_id = before.id
        else:
            reason = ("the test also passes on HEAD — it does not catch the bug (or the fix is "
                      "already committed)")
            return _regression_result(owner, case, rel, name, command, None, now_run, reason, actor)
    else:
        checks = checks_state(owner)
        for run_id in case.repro.get("runs") or []:
            run = checks.get(run_id)
            if run is not None and run.passed and name in run.command:
                before_id = run.id
                break
        if not before_id:
            reason = ("not a git repository and no recorded failing repro ran this test; the "
                      "pre-fix failure can't be shown")
            return _regression_result(owner, case, rel, name, command, None, now_run, reason, actor)
    return _regression_result(owner, case, rel, name, command, before_id, now_run, "", actor)


def _regression_result(owner, case, rel, name, command, before_id, now_run, reason, actor):
    ok = bool(before_id) and now_run.passed
    if not reason and not now_run.passed:
        reason = "the test does not pass on the current tree"
    state = debug_state(owner)
    with state.lock:
        if ok:
            case.regression_test = {"path": rel, "name": name, "command": command,
                                    "failed_before": before_id, "passes_after": now_run.id}
        state.event("regression_test", normalize_actor(actor), case.id,
                    "recorded" if ok else reason)
        _touch(owner, case)
    return {"case": case, "recorded": ok, "reason": reason, "failed_before": before_id,
            "passes_after": now_run}


def close_case(owner: Any, *, cause: str = "", signature: str = "", fix_summary: str = "",
               unreproducible_reason: str = "", actor: str = AGENT) -> Dict[str, Any]:
    case = require_case(owner)
    _require_active(case)
    actor = normalize_actor(actor)
    reason = str(unreproducible_reason or "").strip()
    state = debug_state(owner)
    if reason:
        if case.status == "verified":
            raise GateError("A verified case closes with its root cause, not as unreproducible.",
                            code="wrong_status")
        with state.lock:
            case.status = "unreproducible"
            case.unreproducible_reason = reason
            state.event("unreproducible", actor, case.id, reason)
            _touch(owner, case)
        return {"case": case, "memory_id": "", "gaps": []}
    if case.status != "verified":
        raise GateError(f"Case {case.id} is {case.status}; it closes once verify_fix passes, or "
                        "with an unreproducible_reason.", code="not_verified")
    if not str(cause or "").strip():
        raise ValueError("State the root cause (cause) — the actual defect, not the symptom.")
    files = list(case.fix.get("files") or [])
    root_cause = {"signature": str(signature or case.symptom).strip()[:300],
                  "cause": str(cause).strip(), "fix": str(fix_summary or case.fix.get("summary") or "").strip(),
                  "files": files}
    gaps = [] if case.regression_test else ["no regression test recorded (record_regression_test)"]
    memory_id = _remember_root_cause(owner, case, root_cause)
    with state.lock:
        case.root_cause = root_cause
        case.memory_id = memory_id
        case.status = "closed"
        state.event("closed", actor, case.id, root_cause["cause"][:200])
        _touch(owner, case)
    return {"case": case, "memory_id": memory_id, "gaps": gaps}


def _remember_root_cause(owner: Any, case: DebugCase, root_cause: Dict[str, Any]) -> str:
    session = _session(owner)
    getter = getattr(getattr(session, "session_manager", None), "get_durable_memory_service", None)
    if getter is None or not getattr(session, "variables", {}).get("durable_memory_enabled", True):
        return ""
    statement = (f"Root cause ({root_cause['signature']}): {root_cause['cause']}"
                 + (f" Fix: {root_cause['fix']}" if root_cause["fix"] else "")
                 + (f" Files: {', '.join(root_cause['files'][:8])}" if root_cause["files"] else ""))
    try:
        item, _created = getter().remember(
            session, statement, kind="finding", tags=["debug", "root-cause", *root_cause["files"][:8]],
            source_refs=[{"type": "debug_case", "case": case.id}], actor="model",
            trust_origin="model", verification="verified", confidence=0.9,
            metadata={"debug_case": case.id, "root_cause": root_cause},
        )
        return str(getattr(item, "id", "") or "")
    except Exception:  # noqa: BLE001 — memory is best-effort, the case keeps the record
        return ""


def past_root_causes(session: Any, files: List[str], limit: int = 8) -> List[Dict[str, Any]]:
    getter = getattr(getattr(session, "session_manager", None), "get_durable_memory_service", None)
    if getter is None:
        return []
    try:
        service = getter()
        query = " ".join(["root cause", *[os.path.basename(f) for f in files[:6]]])
        items = service.search_for_session(session, query, limit=limit * 3)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for item in items:
        tags = [str(t).lower() for t in getattr(item, "tags", []) or []]
        if "root-cause" not in tags:
            continue
        meta = getattr(item, "metadata", {}) or {}
        out.append({"id": getattr(item, "id", ""), "statement": getattr(item, "statement", ""),
                    "root_cause": meta.get("root_cause"), "case": meta.get("debug_case", "")})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ bisect (G4)


def bisect_repro(owner: Any, *, good: str, bad: str = "HEAD", timeout: int = 1800,
                 actor: str = AGENT) -> Dict[str, Any]:
    """``git bisect run`` the recorded repro between ``good`` and ``bad`` in a
    separate worktree (the user's tree is never checked out)."""
    case = require_case(owner)
    _require_active(case)
    if not case.repro.get("command"):
        raise GateError("bisect needs an accepted reproducer; record_repro first.",
                        code="repro_required")
    root = workspace_root(owner)
    repo = _repo_root(root)
    if not repo:
        raise GateError("bisect needs a git repository.", code="git_failed")
    for ref in (good, bad):
        if not re.fullmatch(r"[\w./~^@{}-]+", str(ref or "")):
            raise ValueError(f"invalid git revision {ref!r}")
    tmp = tempfile.mkdtemp(prefix="mucli-bisect-")
    tree = os.path.join(tmp, "tree")
    code, out = _git(repo, "worktree", "add", "--detach", tree, bad)
    if code:
        shutil.rmtree(tmp, ignore_errors=True)
        raise GateError(f"could not create a bisect worktree: {out.strip()[:300]}", code="git_failed")
    script = os.path.join(tmp, "probe.sh")
    markers = case.repro["predicate"].get("markers") or []
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/bash\n")
        fh.write(f"out=$(bash -c {_shq(case.repro['command'])} 2>&1); code=$?\n")
        if markers:
            fh.write(f"if grep -qF -- {_shq(markers[0])} <<<\"$out\"; then exit 1; fi\nexit 0\n")
        else:
            fh.write("if [ $code -ne 0 ]; then exit 1; fi\nexit 0\n")
    sub = os.path.relpath(root, repo)
    command = (f"git bisect start {bad} {good} >/dev/null && "
               f"git bisect run bash {_shq(script)}; git bisect log; git bisect reset >/dev/null")
    try:
        run = run_check(CheckSpec(command=command, purpose="explore", predicate={"exit": 0},
                                  cwd=os.path.normpath(os.path.join(tree, sub)), timeout_s=timeout,
                                  ref=case.id, label="git bisect"), owner)
    finally:
        _git(repo, "worktree", "remove", "--force", tree)
        shutil.rmtree(tmp, ignore_errors=True)
    from mu.modes.evidence import read_output

    output = read_output(run)
    match = re.search(r"^([0-9a-f]{7,40}) is the first '?bad'? commit", output, re.M)
    steps = re.findall(r"^# (good|bad|skip): \[([0-9a-f]+)\] (.*)$", output, re.M)
    result = {"first_bad": match.group(1) if match else "", "run": run.id,
              "steps": [{"verdict": v, "commit": c[:12], "subject": s[:120]} for v, c, s in steps],
              "good": good, "bad": bad}
    state = debug_state(owner)
    with state.lock:
        case.bisect = result
        state.event("bisect", normalize_actor(actor), case.id, result["first_bad"] or "inconclusive")
        _touch(owner, case)
    return result


def _shq(text: str) -> str:
    return "'" + str(text).replace("'", "'\"'\"'") + "'"


# ------------------------------------------------------------------ gates + capsule


def edit_gate(session: Any, tool_name: str) -> Optional[Dict[str, Any]]:
    """Refuse the first edit of a turn while the current case has no reproducer."""
    if tool_name not in EDIT_TOOLS:
        return None
    variables = getattr(session, "variables", None) or {}
    if str(variables.get("agent_mode", "default")).lower() != "debug":
        return None
    manager = getattr(session, "session_manager", None)
    states = getattr(manager, "mode_state", None)
    state = states.peek("debug") if states is not None else None
    case = state.current_case() if state is not None else None
    if case is None or case.status != "open":
        return None
    block = bool(variables.get("debug_require_repro", False))
    turn = getattr(session, "_current_turn_start_index", None)
    warned = getattr(session, "_debug_gate_warned", None)
    if not block and warned == (turn, case.id):
        return None
    session._debug_gate_warned = (turn, case.id)
    message = (f"No reproducer recorded for case {case.id} ({case.title}). Record one with "
               "record_repro, or close the case as unreproducible.")
    message += (" Edits stay blocked until then (debug_require_repro is on)." if block else
                " This edit was not applied; repeat it to proceed without a reproducer.")
    return {"ok": False, "error_code": "debug_repro_required", "message": message,
            "data": {"case": case.id, "blocking": block}, "artifacts": [],
            "telemetry": {"tool_name": tool_name, "execution_source": "debug_edit_gate"}}


def _install_hook() -> None:
    from mu.agent.hooks import HookResult, HookSpec, default_registry

    def _hook(ctx):
        envelope = edit_gate(ctx.session, ctx.tool_name or "") if ctx.session is not None else None
        if envelope is None:
            return None
        return HookResult(action="short_circuit", payload=envelope, data={"reason": "debug_repro"})

    default_registry.remove("debug_edit_gate")
    default_registry.add(HookSpec(name="debug_edit_gate", point="pre_tool", priority=20,
                                  handler=_hook))


_install_hook()


def capsule(session: Any) -> str:
    manager = getattr(session, "session_manager", None)
    states = getattr(manager, "mode_state", None)
    state = states.peek("debug") if states is not None else None
    case = state.current_case() if state is not None else None
    if case is None:
        return "No debug case open: open_debug_case(title, symptom) to start."
    lines = [f"Case {case.id} \"{case.title}\" — {case.status.replace('_', ' ')}. Symptom: {case.symptom[:200]}"]
    if case.repro.get("command"):
        lines.append(f"Repro: {case.repro['command'][:160]} (fails: {case.repro.get('expect', '')})")
    for hyp in case.hypotheses[-4:]:
        lines.append(f"{hyp.id} [{hyp.status}] {hyp.statement[:140]}")
    if case.suspects:
        lines.append("Suspects: " + ", ".join(
            f"{s.path}:{s.line}" if s.line else s.path for s in case.suspects[-5:]))
    nxt = {"open": "record_repro", "reproduced": "locate + add_hypothesis/update_hypothesis",
           "root_caused": "fix, then verify_fix", "fixed": "verify_fix until it passes",
           "verified": "record_regression_test (optional), then close_debug_case"}.get(case.status)
    if nxt:
        lines.append(f"Next: {nxt}")
    if (getattr(session, "variables", None) or {}).get("plan_mode"):
        lines.append("Plan mode: runs (record_repro, verify_fix, record_regression_test, "
                     "bisect_repro) are unavailable until /plan off; hypotheses and suspects work.")
    return "\n".join(lines)


def case_summary(case: DebugCase) -> Dict[str, Any]:
    return {"id": case.id, "title": case.title, "status": case.status,
            "hypotheses": len(case.hypotheses), "suspects": len(case.suspects),
            "repro": case.repro.get("command", ""), "updated_at": case.updated_at}


__all__ = [
    "RUN_TOOLS", "add_hypothesis", "add_suspect", "bisect_repro", "capsule", "case_summary",
    "changed_files", "close_case", "edit_gate", "failure_predicate", "fixed_predicate",
    "open_case", "past_root_causes", "record_regression_test", "record_repro", "require_case",
    "select_case", "update_hypothesis", "verify_fix",
]
