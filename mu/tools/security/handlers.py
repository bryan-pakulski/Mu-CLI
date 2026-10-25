"""Approval-gated security tools. All decisions and persistence go through the engine."""
from dataclasses import asdict

from mu.modes.preview import register_preview
from mu.modes.tooling import envelope, guarded
from mu.security import engine
from mu.security.state import security_state
from mu.tools import tool

_STRING = {"type": "string"}
_STRINGS = {"type": "array", "items": _STRING}
_FINDING = {"finding_id": _STRING}
_RUN = {**_FINDING, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800}}

# name -> (description, argument schema, required keys, read-only)
_SPECS = {
    "create_security_report": ("Create and select a security scan; container execution is the default. Then set scope.",
        {"title": _STRING, "summary": _STRING}, ["title"], False),
    "open_security_report": ("Select an existing scan in this workspace by id.", {"scan_id": _STRING}, ["scan_id"], False),
    "list_security_reports": ("List scans in this workspace without selecting one.", {}, [], True),
    "get_security_state": ("Read the active scan, findings, scope, coverage and observations.", {}, [], True),
    "set_security_scope": ("Set the scan's paths and threat model before reviewing or adding findings.",
        {"in_scope_paths": _STRINGS, "entry_points": _STRINGS, "trust_boundaries": _STRINGS,
         "assets": _STRINGS, "attacker": _STRING},
        ["in_scope_paths", "entry_points", "trust_boundaries", "assets", "attacker"], False),
    "scan_attack_surface": ("Discover entry points and known sinks within scope. Optional patterns are literal substrings.",
        {"patterns": _STRINGS}, [], False),
    "review_surface_item": ("Record a reviewed surface item; link a finding or record an observation.",
        {"item_id": _STRING, "verdict": {"type": "string", "enum": ["reviewed_clean", "finding", "observation"]},
         "note": _STRING, "finding_id": _STRING}, ["item_id", "verdict", "note"], False),
    "add_observation": ("Record a hardening note without claiming an exploitable vulnerability.",
        {"title": _STRING, "note": _STRING, "surface_ids": _STRINGS}, ["title", "note"], False),
    "add_security_finding": ("Record an exploit hypothesis with CWE, path:line locations and CVSS 3.1 base vector. Duplicate CWE/sink locations reuse the finding.",
        {"title": _STRING, "summary": _STRING, "vulnerability_class": _STRING,
         "exploit_path": _STRING, "cwe": _STRING, "affected_paths": _STRINGS,
         "cvss_vector": _STRING, "severity_rationale": _STRING, "references": _STRINGS, "surface_ids": _STRINGS},
        ["title", "exploit_path", "cwe", "affected_paths", "cvss_vector", "severity_rationale"], False),
    "attach_security_proof": ("Attach a PoC command run from the workspace root. Declare all PoC scripts/dependencies in files, literal exploit markers, and a distinct probe_marker printed even after the fix. Reattachment clears remediation.",
        {**_FINDING, "command": _STRING, "expected_markers": _STRINGS, "probe_marker": _STRING,
         "files": _STRINGS, "description": _STRING, "kind": _STRING, "expected_exit": {"type": "integer"}},
        ["finding_id", "command", "expected_markers", "probe_marker", "files"], False),
    "verify_security_proof": ("Run the frozen PoC after human review of its command and scripts. Captures immutable CheckRun evidence; markers, exit and probe must match.",
        _RUN, ["finding_id"], False),
    "confirm_security_severity": ("After proof verification, confirm a CVSS vector against the demonstrated impact.",
        {**_FINDING, "cvss_vector": _STRING, "rationale": _STRING, "demonstrated_impact": _STRING},
        ["finding_id", "cvss_vector", "rationale", "demonstrated_impact"], False),
    "attach_remediation_patch": ("Attach a unified diff and regression commands after proof verification. Apply the patch separately before verification.",
        {**_FINDING, "description": _STRING, "patch_diff": _STRING, "regression_checks": _STRINGS},
        ["finding_id", "description", "patch_diff", "regression_checks"], False),
    "verify_remediation": ("Verify patch hunks in the working tree, frozen PoC executes without any exploit markers, and regression commands pass. Requires human command review.",
        _RUN, ["finding_id"], False),
    "approve_security_finding": ("Finalize a finding only after proof, remediation and severity verification, rechecking file integrity.",
        _FINDING, ["finding_id"], False),
    "refute_security_finding": ("Refute an unapproved hypothesis with a reason. Approved findings cannot be refuted.",
        {**_FINDING, "reason": _STRING}, ["finding_id", "reason"], False),
    "security_report": ("Write REPORT.md and per-finding narratives, evidence links, refutations and observations.", {}, [], False),
}


def _execute(name, args, context):
    owner = context
    readonly = _SPECS[name][3]
    if not readonly:
        engine.writable(owner)
    state = security_state(owner)
    with state.lock:
        if name == "create_security_report":
            report = engine.create_security_report(owner=owner, **args)
            return engine.summarize_report(report)
        if name == "list_security_reports":
            return {"reports": engine.list_reports(owner)}
        if name == "open_security_report":
            return engine.summarize_report(engine.open_report(owner, **args))
        report = engine.active_report(owner)
        if name == "get_security_state":
            return {**engine.summarize_report(report), "findings": [asdict(f) for f in report.findings],
                    "surface": report.surface, "observations": report.observations,
                    "runner": engine.runner_for(owner, report)}
        finding_id = args.get("finding_id")
        finding = engine.require_finding(report, finding_id) if finding_id else None
        opts = {k: v for k, v in args.items() if k != "finding_id"}
        try:
            if name == "set_security_scope":
                result = engine.set_scope(report, **args)
            elif name == "scan_attack_surface":
                result = engine.scan_attack_surface(report, **args)
            elif name == "review_surface_item":
                result = engine.review_surface_item(report, **args)
            elif name == "add_observation":
                result = engine.add_observation(report, **args)
            elif name == "add_security_finding":
                result = asdict(engine.add_finding(report, **args))
            elif name == "attach_security_proof":
                engine.attach_proof(finding, **opts)
                result = asdict(finding)
            elif name == "attach_remediation_patch":
                engine.attach_remediation(finding, **opts)
                result = asdict(finding)
            elif name == "confirm_security_severity":
                result = asdict(engine.confirm_severity(finding, **opts))
            elif name in {"verify_security_proof", "verify_remediation"}:
                remediation = name == "verify_remediation"
                engine.consume_preview(owner, finding_id, remediation=remediation)
                fn = engine.verify_remediation if remediation else engine.verify_proof
                fn(finding, report=report, owner=owner, timeout=args.get("timeout_seconds", 60))
                result = asdict(finding)
            elif name == "approve_security_finding":
                result = asdict(engine.approve_finding(finding, report=report, owner=owner))
            elif name == "refute_security_finding":
                result = asdict(engine.refute_finding(finding, **opts))
            elif name == "security_report":
                result = {"path": engine.generate_report(report)}
            else:
                raise ValueError("unknown security operation")
        finally:
            # Failed runs and invalidated verifications must persist too.
            engine.persist(owner, report)
        return result


def _register(name, spec):
    description, properties, required, readonly = spec
    human = name in {"verify_security_proof", "verify_remediation"}

    @tool(name=name, description=description,
          parameters={"type": "object", "properties": properties, "required": required},
          requires_approval=not readonly, approval_policy="always_human" if human else "default",
          phase="security", execution_kind="read" if readonly else "mutate",
          preview_policy="optional" if human else "none", server_policy="session_only")
    @guarded(name)
    def handler(args, context):
        data = _execute(name, args, context)
        if human:
            record = data.get("remediation" if name == "verify_remediation" else "proof") or {}
            if not record.get("verified"):
                return envelope(name, False, record.get("notes", "Verification failed"), data, "verification_failed")
        return envelope(name, True, "Security scan updated." if not readonly else "Security scan state.", data)
    return handler


for _name, _spec in _SPECS.items():
    globals()[_name] = _register(_name, _spec)


@register_preview("verify_security_proof")
def proof_preview(args, session):
    return engine.prepare_preview(session, args["finding_id"])


@register_preview("verify_remediation")
def remediation_preview(args, session):
    return engine.prepare_preview(session, args["finding_id"], remediation=True)
