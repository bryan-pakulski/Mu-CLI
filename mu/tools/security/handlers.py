"""Security-mode `@tool` handlers.

Each `@tool` wrapper delegates to a private `_handle_<tool>` body
defined in this module. The bodies share `_resolve_security_report`,
`_require_report`, and `_save_active_report` helpers (also private)
for managing the active SecurityReport on the session.
"""

import json
import os
from typing import Any, Dict

from mu.tools import tool
from mu.tools.descriptors import ToolExecutionContext


# ----------------------------------------------------------------- helpers


def _resolve_security_report(session):
    """Lazy-load (or open from session) the current SecurityReport."""
    from mu.security.engine import load_report, create_security_report  # noqa
    if session is None:
        return None
    return getattr(session, "_active_security_report", None)


def _require_report(session) -> Any:
    report = _resolve_security_report(session)
    if report is None:
        raise ValueError(
            "no active security report — call `create_security_report` first"
        )
    return report


def _save_active_report(session) -> None:
    from mu.security.engine import save_report
    if session is None:
        return
    report = getattr(session, "_active_security_report", None)
    if report is None:
        return
    sid = getattr(session, "_active_security_session_id", "") or ""
    save_report(sid, report)


def _handle_create_security_report(
    args: dict, context: ToolExecutionContext
) -> str:
    from mu.security.engine import create_security_report, summarize_report
    session = getattr(context, "session", None)
    folder_context = context.folder_context
    session_id = ""
    if session is not None and hasattr(session, "session_manager"):
        session_id = getattr(session.session_manager, "current_session_name", "") or ""
    report = create_security_report(
        title=str(args.get("title", "") or "Security audit"),
        summary=str(args.get("summary", "") or ""),
        folder_context=folder_context,
        session_id=session_id or None,
    )
    if session is not None:
        session._active_security_report = report
        session._active_security_session_id = session_id
    return json.dumps(summarize_report(report), indent=2)


def _handle_add_security_finding(args: dict, context: ToolExecutionContext) -> str:
    from mu.security.engine import add_finding
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding = add_finding(
            report,
            title=str(args.get("title", "") or ""),
            summary=str(args.get("summary", "") or ""),
            vulnerability_class=str(args.get("vulnerability_class", "") or ""),
            severity=str(args.get("severity", "medium") or "medium"),
            affected_paths=list(args.get("affected_paths", []) or []),
            exploit_path=str(args.get("exploit_path", "") or ""),
            references=list(args.get("references", []) or []),
        )
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "title": finding.title,
                "severity": finding.severity,
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_attach_security_proof(
    args: dict, context: ToolExecutionContext
) -> str:
    from mu.security.engine import attach_proof
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        attach_proof(
            finding,
            command=str(args.get("command", "") or ""),
            expected_markers=list(args.get("expected_markers", []) or []),
            description=str(args.get("description", "") or ""),
            kind=str(args.get("kind", "command") or "command"),
        )
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "proof_command": finding.proof.command,
                "expected_markers": finding.proof.expected_markers,
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_verify_security_proof(
    args: dict, context: ToolExecutionContext
) -> str:
    from mu.security.engine import verify_proof
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        cwd = (
            report.workspace_root
            or (
                context.folder_context.folders[0]
                if context.folder_context and context.folder_context.folders
                else os.getcwd()
            )
        )
        timeout = float(args.get("timeout_seconds", 60) or 60)
        verify_proof(finding, cwd=cwd, report=report, timeout=timeout)
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "proof_verified": finding.proof.verified if finding.proof else False,
                "exit_code": finding.proof.exit_code if finding.proof else None,
                "notes": finding.proof.notes if finding.proof else "",
                "stdout_tail": (finding.proof.captured_stdout or "")[-1500:] if finding.proof else "",
                "stderr_tail": (finding.proof.captured_stderr or "")[-1500:] if finding.proof else "",
                "evidence_path": finding.proof.last_run_evidence_path if finding.proof else "",
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_attach_remediation_patch(
    args: dict, context: ToolExecutionContext
) -> str:
    from mu.security.engine import attach_remediation
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        attach_remediation(
            finding,
            description=str(args.get("description", "") or ""),
            patch_diff=str(args.get("patch_diff", "") or ""),
        )
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "patch_chars": len(finding.remediation.patch_diff) if finding.remediation else 0,
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_verify_remediation(
    args: dict, context: ToolExecutionContext
) -> str:
    from mu.security.engine import verify_remediation
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        cwd = (
            report.workspace_root
            or (
                context.folder_context.folders[0]
                if context.folder_context and context.folder_context.folders
                else os.getcwd()
            )
        )
        timeout = float(args.get("timeout_seconds", 60) or 60)
        verify_remediation(finding, cwd=cwd, report=report, timeout=timeout)
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "remediation_verified": finding.remediation.verified if finding.remediation else False,
                "post_patch_exit_code": finding.remediation.post_patch_exit_code if finding.remediation else None,
                "notes": finding.remediation.notes if finding.remediation else "",
                "stdout_tail": (finding.remediation.post_patch_stdout or "")[-1500:] if finding.remediation else "",
                "stderr_tail": (finding.remediation.post_patch_stderr or "")[-1500:] if finding.remediation else "",
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_approve_security_finding(
    args: dict, context: ToolExecutionContext
) -> str:
    from mu.security.engine import approve_finding
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        approve_finding(finding)
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "title": finding.title,
                "severity": finding.severity,
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_refute_security_finding(
    args: dict, context: ToolExecutionContext
) -> str:
    from mu.security.engine import refute_finding
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        refute_finding(finding, reason=str(args.get("reason", "") or ""))
        _save_active_report(session)
        return json.dumps(
            {"finding_id": finding.finding_id, "status": finding.status}, indent=2
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_get_security_state(
    args: dict, context: ToolExecutionContext
) -> str:
    from mu.security.engine import summarize_report
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(summarize_report(report), indent=2)



# ----------------------------------------------------------------- @tool wrappers


@tool(
    name="create_security_report",
    description=(
        "Open a security audit. Returns a scan_id and creates the "
        "persistence directory at documentation/security_scan_<id>/. "
        "Call once at the start of a security-mode session."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short title for the audit."},
            "summary": {"type": "string", "description": "Optional scope / context."},
        },
        "required": ["title"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
)
def create_security_report(args: Dict[str, Any], context) -> str:
    return _handle_create_security_report(args, context)


@tool(
    name="add_security_finding",
    description=(
        "Record a HYPOTHESIS that the workspace has a specific "
        "vulnerability. The finding is provisional until its PoC is "
        "verified. Severity is one of: info, low, medium, high, critical."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "vulnerability_class": {
                "type": "string",
                "description": (
                    "e.g. sql_injection, xss, path_traversal, "
                    "deserialization, command_injection, weak_crypto, "
                    "hardcoded_secret, auth_bypass."
                ),
            },
            "severity": {
                "type": "string",
                "enum": ["info", "low", "medium", "high", "critical"],
            },
            "affected_paths": {"type": "array", "items": {"type": "string"}},
            "exploit_path": {
                "type": "string",
                "description": (
                    "Human-readable trace of how an attacker reaches the sink."
                ),
            },
            "references": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "severity"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
)
def add_security_finding(args: Dict[str, Any], context) -> str:
    return _handle_add_security_finding(args, context)


@tool(
    name="attach_security_proof",
    description=(
        "Attach an executable PoC to a finding. Must be a shell command "
        "run from the workspace root that, when the vulnerability is "
        "present, produces output containing ALL of the declared "
        "`expected_markers`. The markers are checked literally — use "
        "unique strings ('PWNED-1234', a fabricated secret, a stack-trace "
        "fragment) to avoid false positives."
    ),
    parameters={
        "type": "object",
        "properties": {
            "finding_id": {"type": "string"},
            "command": {"type": "string"},
            "expected_markers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Unique substrings that must appear in stdout+stderr "
                    "when the exploit succeeds."
                ),
            },
            "description": {"type": "string"},
            "kind": {"type": "string", "default": "command"},
        },
        "required": ["finding_id", "command", "expected_markers"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
)
def attach_security_proof(args: Dict[str, Any], context) -> str:
    return _handle_attach_security_proof(args, context)


@tool(
    name="verify_security_proof",
    description=(
        "Execute the attached PoC and decide whether the exploit "
        "actually triggers. Engine runs the command, captures output, "
        "and checks that every declared `expected_marker` literally "
        "appears. Returns ok=True only if all markers match. This is "
        "the anti-hallucination gate — claims aren't believed without "
        "this passing."
    ),
    parameters={
        "type": "object",
        "properties": {
            "finding_id": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 60},
        },
        "required": ["finding_id"],
    },
    requires_approval=True,
    execution_kind="mutate",
    preview_policy="optional",
    server_policy="session_only",
)
def verify_security_proof(args: Dict[str, Any], context) -> str:
    return _handle_verify_security_proof(args, context)


@tool(
    name="attach_remediation_patch",
    description=(
        "Attach a proposed patch (unified diff) to a finding whose PoC "
        "has already been verified. Engine refuses if proof.verified "
        "is False."
    ),
    parameters={
        "type": "object",
        "properties": {
            "finding_id": {"type": "string"},
            "description": {
                "type": "string",
                "description": (
                    "Defensive principle: parameterized queries, "
                    "context-aware escaping, etc."
                ),
            },
            "patch_diff": {
                "type": "string",
                "description": (
                    "Unified diff. Apply with `apply_diff` separately "
                    "so the working tree is modified before remediation "
                    "verification."
                ),
            },
        },
        "required": ["finding_id", "description", "patch_diff"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
)
def attach_remediation_patch(args: Dict[str, Any], context) -> str:
    return _handle_attach_remediation_patch(args, context)


@tool(
    name="verify_remediation",
    description=(
        "Re-run the original PoC against the now-patched workspace. "
        "The exploit must no longer trigger (the markers must be "
        "absent). Returns ok=True only when the patch actually closes "
        "the hole. Apply the patch (via `apply_diff`) BEFORE calling this."
    ),
    parameters={
        "type": "object",
        "properties": {
            "finding_id": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 60},
        },
        "required": ["finding_id"],
    },
    requires_approval=True,
    execution_kind="mutate",
    preview_policy="optional",
    server_policy="session_only",
)
def verify_remediation(args: Dict[str, Any], context) -> str:
    return _handle_verify_remediation(args, context)


@tool(
    name="approve_security_finding",
    description=(
        "Finalize a finding. HARD-GATED: refuses unless both "
        "`proof.verified` and `remediation.verified` are True."
    ),
    parameters={
        "type": "object",
        "properties": {"finding_id": {"type": "string"}},
        "required": ["finding_id"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
)
def approve_security_finding(args: Dict[str, Any], context) -> str:
    return _handle_approve_security_finding(args, context)


@tool(
    name="refute_security_finding",
    description=(
        "Abandon a finding whose PoC couldn't be made to trigger after "
        "multiple revisions. Records the failed hypothesis in the audit "
        "trail rather than silently dropping it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "finding_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["finding_id", "reason"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
)
def refute_security_finding(args: Dict[str, Any], context) -> str:
    return _handle_refute_security_finding(args, context)


@tool(
    name="get_security_state",
    description=(
        "Return the current scan's summary: total findings, by-status "
        "and by-severity counts, and the list of approved findings."
    ),
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    server_policy="session_only",
)
def get_security_state(args: Dict[str, Any], context) -> str:
    return _handle_get_security_state(args, context)
