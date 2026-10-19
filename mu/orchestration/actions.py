"""Available actions (plan §3.4), shared by web, mobile and CLI — the jobs-core pattern.

``*_actions(...)`` return every command for a subject with ``enabled`` and, when disabled, a
human ``reason``. ``commands.dispatch`` re-checks the same rules and refuses a disabled command
with HTTP 409, so availability logic exists exactly once.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from mu.jobs.core.actions import ActionVM

from .models import Node, Run, RunbookVersion, ToolVersion
from .states import EDITABLE, RUN_TERMINAL

NODE_COMMANDS: Sequence[Tuple[str, str]] = (
    ("acceptNode", "Accept"), ("quarantineNode", "Quarantine"), ("unquarantineNode", "Release quarantine"),
    ("revokeNode", "Revoke"), ("setNodeLabels", "Edit labels"), ("setNodeCanary", "Canary"),
    ("rotateCredential", "Rotate credential"), ("refreshFacts", "Refresh facts"),
)
TOOL_COMMANDS: Sequence[Tuple[str, str]] = (
    ("lintDraft", "Run lint"), ("testDraft", "Test on canary"), ("submitReview", "Submit for review"),
    ("requestChanges", "Request changes"), ("publishVersion", "Publish"), ("deprecateVersion", "Deprecate"),
    ("undeprecateVersion", "Restore"), ("retireVersion", "Retire"), ("newVersion", "New version"),
    ("discardDraft", "Discard draft"), ("runTool", "Run"),
)
RUNBOOK_COMMANDS: Sequence[Tuple[str, str]] = (
    ("editRunbook", "Edit draft"), ("testRunbook", "Test on canary"), ("submitRunbook", "Submit for review"),
    ("requestRunbookChanges", "Request changes"), ("publishRunbook", "Publish"), ("deprecateRunbook", "Deprecate"),
    ("retireRunbook", "Retire"), ("newRunbookVersion", "New version"), ("discardRunbookDraft", "Discard draft"),
    ("runRunbook", "Run"),
)
RUN_COMMANDS: Sequence[Tuple[str, str]] = (
    ("approveRun", "Approve and start"), ("rejectRun", "Reject"), ("continueRun", "Continue"),
    ("pauseRun", "Pause"), ("rollbackRun", "Roll back"), ("cancelRun", "Cancel"),
)

TONES = {"acceptNode": "ok", "quarantineNode": "warn", "revokeNode": "danger", "publishVersion": "ok",
         "requestChanges": "warn", "discardDraft": "danger", "retireVersion": "danger", "approveRun": "ok",
         "rejectRun": "danger", "rollbackRun": "warn", "cancelRun": "danger", "continueRun": "ok",
         "publishRunbook": "ok", "deprecateVersion": "warn", "deprecateRunbook": "warn", "pauseRun": "warn",
         "discardRunbookDraft": "danger", "retireRunbook": "danger"}
CONFIRMS = {
    "revokeNode": "Revoke this node? Its credential is deleted and live executions become unreachable. "
                  "It must enrol again with a new join token.",
    "quarantineNode": "Quarantine this node? It keeps reporting but receives no work until released.",
    "discardDraft": "Discard this draft version? This cannot be undone.",
    "discardRunbookDraft": "Discard this draft runbook version? This cannot be undone.",
    "retireRunbook": "Retire this runbook version? Schedules that target it stop working.",
    "retireVersion": "Retire this version? Runbooks pinned to it stop working.",
    "rollbackRun": "Roll back this run? Every completed change with a rollback tool is undone, newest first.",
    "cancelRun": "Cancel this run? Queued work is dropped and nodes are told to stop running executions.",
    "rejectRun": "Reject this proposed run?",
}
INPUTS = {
    "requestChanges": {"label": "What should change?", "optional": False, "multiline": True, "arg": "notes"},
    "requestRunbookChanges": {"label": "What should change?", "optional": True, "multiline": True, "arg": "notes"},
    "rejectRun": {"label": "Reason (optional)", "optional": True, "multiline": False, "arg": "reason"},
    "quarantineNode": {"label": "Reason (optional)", "optional": True, "multiline": False, "arg": "reason"},
}
DIALOGS = {"setNodeLabels": "labels", "testDraft": "test", "runTool": "run", "runRunbook": "run",
           "editRunbook": "edit", "testRunbook": "test"}


def _vm(cmd: str, label: str, reason: Optional[str], *, primary: bool = False, typed: str = "",
        hidden: bool = False) -> ActionVM:
    vm = ActionVM(id=cmd, label=label, enabled=reason is None, reason=reason or "", primary=primary,
                  danger=TONES.get(cmd) == "danger", tone=TONES.get(cmd, ""), confirm=CONFIRMS.get(cmd),
                  input=INPUTS.get(cmd), dialog=DIALOGS.get(cmd), hidden=hidden)
    if typed:
        vm.input = {"label": f"Type {typed} to confirm", "optional": False, "multiline": False, "arg": "typed",
                    "expect": typed}
    return vm


def node_actions(node: Node) -> List[ActionVM]:
    s = node.status
    rules = {
        "acceptNode": None if s == "pending" else "node is not awaiting acceptance",
        "quarantineNode": None if s in ("active", "pending") else f"node is {s}",
        "unquarantineNode": None if s == "quarantined" else "node is not quarantined",
        "revokeNode": None if s != "revoked" else "node is already revoked",
        "setNodeLabels": None if s != "revoked" else "node is revoked",
        "setNodeCanary": None if s != "revoked" else "node is revoked",
        "rotateCredential": None if s == "active" else "only active nodes rotate credentials",
        "refreshFacts": None if s in ("active", "pending", "quarantined") else f"node is {s}",
    }
    out = []
    for cmd, label in NODE_COMMANDS:
        if cmd == "setNodeCanary":
            label = "Unmark canary" if node.canary else "Mark canary"
        out.append(_vm(cmd, label, rules[cmd], primary=cmd == "acceptNode"))
    return out


def tool_actions(tv: ToolVersion, *, lint_ok: bool = False, has_canary: bool = False) -> List[ActionVM]:
    s = tv.status
    rules = {
        "lintDraft": None if s in EDITABLE | {"in_review"} else "published versions are immutable",
        "testDraft": (None if has_canary else "no active canary node; mark a node canary first")
        if s in EDITABLE | {"in_review"} else "only unpublished versions are tested here",
        "submitReview": None if s in EDITABLE else "only drafts are submitted",
        "requestChanges": None if s == "in_review" else "version is not in review",
        "publishVersion": (None if lint_ok else "lint must pass first") if s == "in_review" else "version is not in review",
        "deprecateVersion": None if s == "published" else "only published versions are deprecated",
        "undeprecateVersion": None if s == "deprecated" else "version is not deprecated",
        "retireVersion": None if s == "deprecated" else "deprecate the version first",
        "newVersion": None if s in ("published", "deprecated") else "edit the open draft instead",
        "discardDraft": None if s in EDITABLE | {"in_review"} else "published versions cannot be discarded",
        "runTool": None if s == "published" else "only published versions run on the fleet",
    }
    typed = tv.tool_id if tv.effect == "destructive" else ""
    return [_vm(cmd, label, rules[cmd], primary=cmd in ("publishVersion", "runTool"),
                typed=typed if cmd == "publishVersion" else "") for cmd, label in TOOL_COMMANDS]


def _fix_first(problems: Sequence[str]) -> Optional[str]:
    if not problems:
        return None
    n = len(problems)
    return f"fix {n} problem{'s' if n != 1 else ''} first: {problems[0]}"


def runbook_actions(rb: RunbookVersion, problems: Sequence[str] = (),
                    review_problems: Optional[Sequence[str]] = None, *, has_canary: bool = False,
                    open_draft: str = "") -> List[ActionVM]:
    """``problems``: blockers for publishing; ``review_problems``: blockers for review (tools
    may still be in review then). Defaults to ``problems``. ``open_draft`` names another version
    of this runbook that is already open (a new version can't start then)."""
    s = rb.status
    review_problems = problems if review_problems is None else review_problems
    invalid = ("runbook is invalid: " + problems[0]) if problems else None
    unparsed = "the YAML doesn't parse; fix it first" if rb.source else None
    rules = {
        "editRunbook": None if s == "draft" else
        ("in review: request changes to edit it" if s == "in_review" else "published versions are immutable"),
        "testRunbook": (unparsed or _fix_first(problems) or
                        (None if has_canary else "no active canary node; mark a node canary first"))
        if s in ("draft", "in_review") else "only drafts are tested here",
        "submitRunbook": (unparsed or _fix_first(review_problems)) if s == "draft" else "only drafts are submitted",
        "requestRunbookChanges": None if s == "in_review" else "runbook is not in review",
        "publishRunbook": (invalid if s == "in_review" else "runbook is not in review"),
        "deprecateRunbook": None if s == "published" else "only published runbooks are deprecated",
        "retireRunbook": None if s == "deprecated" else "deprecate the runbook first",
        "newRunbookVersion": (f"{open_draft} is already open; edit it instead" if open_draft else None)
        if s in ("published", "deprecated") else "edit the open draft instead",
        "discardRunbookDraft": None if s in ("draft", "in_review") else "published versions cannot be discarded",
        "runRunbook": None if s == "published" else "only published runbooks run",
    }
    hidden = {"editRunbook": s not in ("draft", "in_review"), "newRunbookVersion": s in ("draft", "in_review"),
              "testRunbook": s not in ("draft", "in_review"), "discardRunbookDraft": s not in ("draft", "in_review")}
    return [_vm(cmd, label, rules[cmd], primary=cmd in ("publishRunbook", "runRunbook", "editRunbook"),
                hidden=hidden.get(cmd, False)) for cmd, label in RUNBOOK_COMMANDS]


def run_actions(run: Run, *, has_rollback: bool = False) -> List[ActionVM]:
    s = run.state
    level = (run.decision or {}).get("level")
    rules = {
        "approveRun": None if s == "proposed" and level != "deny" else
        ("denied by access policy" if s == "proposed" else "run is not awaiting approval"),
        "rejectRun": None if s == "proposed" else "run is not awaiting approval",
        "continueRun": None if s == "paused" else "run is not paused",
        "pauseRun": None if s == "running" else "run is not running",
        "rollbackRun": (None if has_rollback else "no completed change has a rollback tool")
        if s in ("running", "paused", "succeeded", "failed") and run.kind not in ("test", "runbook_test")
        else "run cannot roll back now",
        "cancelRun": None if s not in RUN_TERMINAL and s != "rolling_back" else f"run is {s}",
    }
    typed = run.target_id if level == "typed" else ""
    return [_vm(cmd, label, rules[cmd], primary=cmd in ("approveRun", "continueRun"),
                typed=typed if cmd == "approveRun" else "") for cmd, label in RUN_COMMANDS]


def check_allowed(actions: List[ActionVM], command: str) -> Optional[str]:
    for action in actions:
        if action.id == command:
            return None if action.enabled else action.reason
    return f"unknown command {command}"


def as_dicts(actions: List[ActionVM]) -> List[Dict[str, Any]]:
    return [a.to_dict() for a in actions]
