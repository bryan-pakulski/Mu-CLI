"""Security-mode `@tool` registrations.

Descriptor-only move. Bodies remain in `core/tools.py` as
`_handle_<tool>` until `core/security_mode.py` itself relocates.
"""

from typing import Any, Dict

from mu.tools import tool


def _legacy(handler_name: str):
    """Resolve the legacy `_handle_<tool>` function on `core.tools`."""

    from core import tools as _legacy_tools

    handler = getattr(_legacy_tools, handler_name, None)
    if handler is None:
        raise RuntimeError(
            f"Legacy security handler {handler_name!r} missing from core.tools."
        )
    return handler


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
    return _legacy("_handle_create_security_report")(args, context)


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
    return _legacy("_handle_add_security_finding")(args, context)


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
    return _legacy("_handle_attach_security_proof")(args, context)


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
    return _legacy("_handle_verify_security_proof")(args, context)


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
    return _legacy("_handle_attach_remediation_patch")(args, context)


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
    return _legacy("_handle_verify_remediation")(args, context)


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
    return _legacy("_handle_approve_security_finding")(args, context)


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
    return _legacy("_handle_refute_security_finding")(args, context)


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
    return _legacy("_handle_get_security_state")(args, context)
