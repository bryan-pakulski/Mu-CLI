"""Security audit gates, durable scans, coverage and verified evidence.

Every surface uses this engine. Command execution delegates to run_check; host use
requires an operator's per-scan choice, while Docker is the default.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import asdict, fields
from pathlib import Path
from types import SimpleNamespace

from mu.modes.evidence import CheckRefused, CheckSpec, hash_files, read_output, run_check, scrubbed_env, workspace_root
from mu.modes.state import invalidate_cached, load_cached
from mu.security.cvss import score_vector
from mu.security.models import SecurityFinding, SecurityProof, SecurityRemediation, SecurityReport
from mu.security.secret_paths import is_denied_path, redact_secrets
from mu.security.state import security_state

FINDING_STATUSES = {"new", "proof_attached", "exploit_verified", "remediation_attached",
                    "remediation_verified", "approved", "refuted"}
SEVERITY_LEVELS = ("info", "low", "medium", "high", "critical")


def _variables(owner):
    return getattr(owner, "variables", None) or getattr(getattr(owner, "session", None), "variables", {}) or {}


def writable(owner):
    if _variables(owner).get("plan_mode"):
        raise CheckRefused("Plan mode is active — security changes and runs are blocked.", "plan_mode_blocked")


def _within(root, path):
    root, path = os.path.realpath(root), os.path.realpath(path)
    return os.path.commonpath([root, path]) == root


def safe_path(root, value, *, exists=True):
    """Resolve a workspace path, rejecting traversal, symlink escapes and secrets."""
    path = os.path.realpath(os.path.join(root, str(value)))
    if not _within(root, path) or is_denied_path(path)[0]:
        raise ValueError(f"path is outside the workspace or protected: {value}")
    if exists and not os.path.exists(path):
        raise ValueError(f"path does not exist: {value}")
    return path


def _scan_directory(root, scan_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", scan_id):
        raise ValueError("invalid scan id")
    return safe_path(root, f"documentation/security_scan_{scan_id}", exists=False)


def _atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".security-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _status(report):
    if (report.findings or report.surface_scanned) and all(f.status in {"approved", "refuted"} for f in report.findings) and all(s["status"] != "unreviewed" for s in report.surface):
        report.status = "complete"
    elif any(f.runs for f in report.findings):
        report.status = "verifying"
    else:
        report.status = "scanning"


def save_report(session_id, report):
    _status(report)
    report.updated_at = time.time()
    report.directory = _scan_directory(report.workspace_root, report.scan_id)
    report.metadata_path = safe_path(report.workspace_root, os.path.join(report.directory, "report.json"), exists=False)
    _atomic(report.metadata_path, json.dumps(asdict(report), indent=2))
    invalidate_cached(report.metadata_path)
    return report


def _record(cls, raw):
    return cls(**{k: v for k, v in raw.items() if k in {f.name for f in fields(cls)}})


def load_report(metadata_path):
    try:
        raw = json.loads(Path(metadata_path).read_text())
        findings = []
        for data in raw.get("findings") or []:
            data = dict(data)
            for key, cls in (("proof", SecurityProof), ("remediation", SecurityRemediation)):
                val = data.get(key)
                data[key] = _record(cls, val) if isinstance(val, dict) else None
            proof = data.get("proof")
            if proof and (not proof.command.strip() or not proof.expected_markers):
                data["proof"] = None
            findings.append(_record(SecurityFinding, data))
        raw["findings"] = findings
        report = _record(SecurityReport, raw)
        # Physical location is authoritative, not paths inside report.json.
        report.directory = os.path.realpath(os.path.dirname(metadata_path))
        report.workspace_root = str(Path(report.directory).parent.parent)
        report.metadata_path = os.path.realpath(metadata_path)
        if report.directory != _scan_directory(report.workspace_root, report.scan_id):
            return None
        if any(not re.fullmatch(r"f[0-9]+", f.finding_id) for f in findings):
            return None
        return report
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def active_report(owner, *, required=True):
    state = security_state(owner)
    with state.lock:
        path = state.reports.get(state.active, "")
        if path:
            try:
                path = safe_path(workspace_root(owner), path)
                report = copy.deepcopy(load_cached(path, load_report))
            except (OSError, ValueError):
                report = None
            if report and report.scan_id == state.active and report.workspace_root == os.path.realpath(workspace_root(owner)):
                return report
    if required:
        raise ValueError("no active security report — create or open a scan first")
    return None


def persist(owner, report):
    writable(owner)
    save_report("", report)
    state = security_state(owner)
    state.reports[report.scan_id] = report.metadata_path
    state.active = report.scan_id
    state.changed()
    return report


def create_security_report(*, title, summary="", folder_context=None, session_id=None, scan_id=None, owner=None):
    writable(owner)
    root = workspace_root(owner or SimpleNamespace(folder_context=folder_context))
    sid = re.sub(r"[^a-zA-Z0-9]+", "_", scan_id or f"{title}_{uuid.uuid4().hex[:8]}").strip("_").lower() or uuid.uuid4().hex[:8]
    directory = _scan_directory(root, sid)
    if os.path.exists(directory):
        raise ValueError("scan already exists; open it instead")
    report = SecurityReport(scan_id=sid, title=title.strip() or "Security audit", summary=summary,
                            workspace_root=root, directory=directory)
    for sub in ("findings", "proofs", "patches"):
        Path(directory, sub).mkdir(parents=True, exist_ok=True)
    _atomic(Path(directory, "README.md"), f"# {report.title}\n\nScan `{sid}`. Generate REPORT.md after review.\n")
    return persist(owner, report) if owner is not None else save_report(session_id or "", report)


def list_reports(owner):
    # Explicit discovery is read-only. It never selects another session's scan.
    root = workspace_root(owner)
    reports = []
    for path in sorted(Path(root, "documentation").glob("security_scan_*/report.json")):
        if not _within(root, path):
            continue
        report = load_report(str(path))
        if report:
            reports.append(summarize_report(report))
    return reports


def open_report(owner, scan_id):
    writable(owner)
    path = os.path.join(_scan_directory(workspace_root(owner), scan_id), "report.json")
    report = load_report(path)
    if report is None:
        raise ValueError("scan not found in this workspace")
    state = security_state(owner)
    state.active = report.scan_id
    state.reports[report.scan_id] = path
    state.changed()
    return report


def configure_runner(owner, runner, *, actor):
    writable(owner)
    if not actor.startswith("user:"):
        raise ValueError("only the user can choose host execution")
    if runner not in {"host", "docker", "auto"}:
        raise ValueError("runner must be auto, docker or host")
    report = active_report(owner)
    state = security_state(owner)
    state.execution[report.scan_id] = {"runner": runner, "host_allowed": runner == "host",
                                       "actor": actor, "at": time.time()}
    state.previews.clear()
    state.changed()
    return state.execution[report.scan_id]


def runner_for(owner, report):
    from mu.tools.capabilities import normalize_session_type
    choice = security_state(owner).execution.get(report.scan_id, {})
    if normalize_session_type(_variables(owner).get("session_type")) == "container":
        return "container"  # the worker already lives in the session's isolated container
    if choice.get("runner") == "host" and choice.get("host_allowed") is True:
        return "host"
    return "docker"  # never silently fall back to the host


def set_scope(report, *, in_scope_paths, entry_points, trust_boundaries, assets, attacker):
    if report.findings or report.surface:
        raise ValueError("scope is frozen after review starts; create a new scan to change it")
    if not in_scope_paths or not attacker.strip() or not assets or not trust_boundaries:
        raise ValueError("scope needs paths, assets, trust boundaries and an attacker model")
    paths = [os.path.relpath(safe_path(report.workspace_root, p), report.workspace_root) for p in in_scope_paths]
    report.scope = dict(in_scope_paths=paths, entry_points=list(entry_points),
                        trust_boundaries=list(trust_boundaries), assets=list(assets), attacker=attacker.strip())
    return report.scope


def _in_scope(report, path):
    full = safe_path(report.workspace_root, path)
    if not report.scope:
        raise ValueError("set_security_scope must come first")
    if not any(_within(os.path.join(report.workspace_root, p), full) for p in report.scope["in_scope_paths"]):
        raise ValueError(f"path is outside scan scope: {path}")
    return full


def _location(report, location):
    path, sep, line = str(location).rpartition(":")
    if not sep or not line.isdigit() or int(line) < 1:
        raise ValueError("affected locations must be path:line")
    full = _in_scope(report, path)
    if not os.path.isfile(full) or int(line) > len(Path(full).read_text(errors="replace").splitlines()):
        raise ValueError(f"invalid source location: {location}")
    return f"{os.path.relpath(full, report.workspace_root)}:{int(line)}"


def _editable(finding):
    if finding.status in {"approved", "refuted"}:
        raise ValueError(f"{finding.status} findings are final; create a new hypothesis")


def add_finding(report, *, title, exploit_path, cwe, affected_paths, cvss_vector, severity_rationale,
                summary="", vulnerability_class="", references=None, surface_ids=None):
    locations = [_location(report, p) for p in affected_paths]
    if not locations or not exploit_path.strip() or not re.fullmatch(r"CWE-[1-9][0-9]*", cwe):
        raise ValueError("a finding needs affected path:line locations, exploit_path and CWE-N")
    if not severity_rationale.strip():
        raise ValueError("a severity rationale is required")
    score, severity = score_vector(cvss_vector)
    items = {item["id"]: item for item in report.surface}
    for sid in surface_ids or []:
        if sid not in items:
            raise ValueError(f"unknown surface item: {sid}")
    for f in report.findings:
        if f.cwe == cwe and f.affected_paths and f.affected_paths[0] == locations[0]:
            return f  # preserve evidence and decisions when a duplicate is proposed
    high = max((int(f.finding_id[1:]) for f in report.findings), default=0)
    f = SecurityFinding(finding_id=f"f{high+1:03d}", title=title.strip(), summary=summary,
                        cwe=cwe, affected_paths=locations, exploit_path=exploit_path,
                        cvss_vector=cvss_vector, cvss_score=score, severity=severity,
                        severity_rationale=severity_rationale, vulnerability_class=vulnerability_class,
                        references=list(references or []), surface_ids=list(surface_ids or []))
    report.findings.append(f)
    for sid in f.surface_ids:
        items[sid].update(status="finding", finding_id=f.finding_id)
    return f


def attach_proof(finding, *, command, expected_markers, description="", kind="command",
                 files=None, probe_marker="", expected_exit=0):
    _editable(finding)
    markers = [str(m) for m in expected_markers if str(m).strip()]
    if not command.strip() or not markers:
        raise ValueError("proof needs a command and non-empty expected markers")
    if probe_marker and any(m in probe_marker or probe_marker in m for m in markers):
        raise ValueError("probe marker must be distinct from exploit markers")
    finding.proof = SecurityProof(command=command.strip(), expected_markers=markers,
                                  description=description, kind=kind, files=list(files or []),
                                  probe_marker=probe_marker, expected_exit=int(expected_exit))
    finding.remediation = None
    finding.severity_confirmed = False
    finding.status = "proof_attached"
    return finding.proof


def attach_remediation(finding, *, description, patch_diff, regression_checks, patch_applied_path=""):
    _editable(finding)
    if not finding.proof or not finding.proof.verified:
        raise ValueError("remediation requires a verified proof")
    if not patch_diff.strip() or not regression_checks or not all(str(c).strip() for c in regression_checks):
        raise ValueError("remediation needs a unified diff and regression check commands")
    if not finding.proof.probe_marker:
        raise ValueError("attach and verify a proof with a probe_marker before remediation")
    finding.remediation = SecurityRemediation(description=description, patch_diff=patch_diff.rstrip()+"\n",
                                              regression_checks=list(regression_checks))
    finding.status = "remediation_attached"
    return finding.remediation


def confirm_severity(finding, *, cvss_vector, rationale, demonstrated_impact):
    _editable(finding)
    if not finding.proof or not finding.proof.verified or not demonstrated_impact.strip() or not rationale.strip():
        raise ValueError("severity confirmation needs a verified proof, rationale and demonstrated impact")
    finding.cvss_score, finding.severity = score_vector(cvss_vector)
    finding.cvss_vector, finding.severity_rationale = cvss_vector, rationale
    finding.demonstrated_impact, finding.severity_confirmed = demonstrated_impact, True
    return finding


def proof_bundle(proof, root, owner):
    """Hash declared PoC dependencies and directly invoked scripts; inspect their contents."""
    from mu.agent.secret_guard import command_refusal
    reason = command_refusal(proof.command, _variables(owner))
    if reason:
        raise CheckRefused(f"Secret-guard blocked the PoC: {reason}", "secret_guard_blocked")
    paths = set(proof.files)
    try:
        tokens = shlex.split(proof.command)
    except ValueError as exc:
        raise ValueError("invalid PoC shell quoting") from exc
    script_next = False
    inline_next = False
    interpreters = {"bash", "sh", "dash", "zsh", "python", "python3", "node", "ruby", "perl", "source", "."}
    for token in tokens:
        if inline_next:
            inline_next = False
            script_next = False
            continue
        if script_next and token in {"-c", "-m", "-e", "--eval", "--command"}:
            inline_next = True
            continue
        invoked = script_next and not token.startswith("-")
        if invoked:
            script_next = False
        if os.path.basename(token) in interpreters:
            script_next = True
            continue
        # Include extensionless interpreter scripts and declared dependencies too.
        if invoked or token.endswith((".py", ".sh", ".js", ".cjs", ".mjs", ".rb", ".pl")) or token.startswith("./"):
            full = safe_path(root, token)
            if os.path.isfile(full):
                paths.add(os.path.relpath(full, root))
    scripts = {}
    for path in sorted(paths):
        full = safe_path(root, path)
        if not os.path.isfile(full) or os.path.getsize(full) > 256_000:
            raise ValueError("PoC files must be regular files of at most 256 KB")
        text = Path(full).read_text(errors="replace")
        reason = command_refusal(text, _variables(owner))
        if reason:
            raise CheckRefused(f"Secret-guard blocked PoC file {path}: {reason}", "secret_guard_blocked")
        scripts[os.path.relpath(full, root)] = text
    hashes = hash_files(scripts, root)
    data = {"command": proof.command, "markers": proof.expected_markers,
            "probe_marker": proof.probe_marker, "exit": proof.expected_exit, "hashes": hashes}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return hashes, scripts, digest


def execution_preview(owner, finding_id, *, remediation=False):
    report = active_report(owner)
    finding = require_finding(report, finding_id)
    if finding.proof is None:
        raise ValueError("attach a proof first")
    hashes, scripts, digest = proof_bundle(finding.proof, report.workspace_root, owner)
    preview = {"scan_id": report.scan_id, "finding_id": finding_id,
               "command": finding.proof.command, "cwd": report.workspace_root,
               "runner": runner_for(owner, report), "scripts": scripts, "hashes": hashes,
               "proof_digest": digest, "probe_marker": finding.proof.probe_marker,
               "expected_markers": finding.proof.expected_markers,
               "effect": "Executes code against the real workspace" if runner_for(owner, report) == "host"
                         else "Executes inside a container; Docker checks use a disposable copy with network off"}
    if remediation:
        rem = finding.remediation
        if rem is None:
            raise ValueError("attach a remediation first")
        regression_bundles = []
        for command in rem.regression_checks:
            dep_hashes, dep_scripts, dep_digest = proof_bundle(SecurityProof(command=command), report.workspace_root, owner)
            regression_bundles.append({"command": command, "hashes": dep_hashes, "scripts": dep_scripts, "digest": dep_digest})
        preview.update(regression_checks=rem.regression_checks, regression_bundles=regression_bundles, patch_diff=rem.patch_diff)
    return preview


def prepare_preview(owner, finding_id, *, remediation=False):
    preview = execution_preview(owner, finding_id, remediation=remediation)
    state = security_state(owner)
    state.previews[(finding_id, remediation)] = copy.deepcopy(preview)
    # Redact any literal credentials before presenting command/script contents.
    return json.loads(redact_secrets(json.dumps(preview))[0])


def consume_preview(owner, finding_id, *, remediation=False):
    state = security_state(owner)
    approved = state.previews.pop((finding_id, remediation), None)
    current = execution_preview(owner, finding_id, remediation=remediation)
    if approved is None or approved != current:
        raise CheckRefused("The PoC or execution settings changed, or no approval preview exists. Review and approve the run again.",
                           "approval_stale")


def require_finding(report, finding_id):
    finding = report.find(finding_id)
    if finding is None:
        raise ValueError(f"no such finding: {finding_id}")
    return finding


def _run(owner, report, finding, command, predicate, purpose, timeout, hashes):
    runner = runner_for(owner, report)
    bundle_proof = finding.proof if command == finding.proof.command else SecurityProof(command=command)
    dep_hashes, dep_scripts, dep_digest = proof_bundle(bundle_proof, report.workspace_root, owner)
    spec = CheckSpec(command=command, purpose=purpose, predicate=predicate, cwd=report.workspace_root,
                     timeout_s=int(timeout), hash_paths=list(hashes | dep_hashes), runner="host" if runner == "container" else runner,
                     network=False, ref=f"{report.scan_id}/{finding.finding_id}")
    try:
        run = run_check(spec, owner)
    except RuntimeError as exc:
        raise CheckRefused(f"{exc}. Select /security runner host to opt this scan into host execution.", "runner_unavailable") from exc
    data = run.to_dict()
    # Evidence is immutable per run, including reruns after failed verification.
    directory = safe_path(report.workspace_root, os.path.join(report.directory, "proofs", finding.finding_id,
                                                             f"{run.id}-{uuid.uuid4().hex[:8]}"), exists=False)
    _atomic(os.path.join(directory, "output.txt"), read_output(run))
    _atomic(os.path.join(directory, "bundle.json"), redact_secrets(json.dumps(
        {"command": command, "hashes": dep_hashes, "scripts": dep_scripts, "digest": dep_digest}, indent=2))[0])
    # The short CheckRun id is only unique inside its originating session. A scan
    # can be reopened elsewhere, so persisted run rows use the immutable directory id.
    data["check_id"] = run.id
    data["id"] = os.path.basename(directory)
    data["evidence_path"] = os.path.relpath(directory, report.directory)
    _atomic(os.path.join(directory, "run.json"), json.dumps(data, indent=2))
    finding.runs.append(data)
    return run, directory


def verify_proof(finding, *, cwd=None, report=None, timeout=60, owner=None):
    writable(owner)
    _editable(finding)
    proof = finding.proof
    if proof is None:
        raise ValueError("attach a proof before verifying")
    if not proof.expected_markers:
        raise ValueError("proof has no markers; verification would be vacuous")
    if report is None or owner is None:
        raise ValueError("verification requires a session and active report")
    hashes, _, digest = proof_bundle(proof, report.workspace_root, owner)
    finding.remediation = None
    finding.severity_confirmed = False
    proof.verified, proof.verified_at = False, None
    finding.status = "proof_attached"
    predicate = {"exit": proof.expected_exit, "markers": proof.expected_markers + ([proof.probe_marker] if proof.probe_marker else [])}
    run, directory = _run(owner, report, finding, proof.command, predicate, "poc", timeout, hashes)
    # A verifier that rewrites itself cannot establish a stable proof bundle.
    after, _, after_digest = proof_bundle(proof, report.workspace_root, owner)
    proof.verified = run.passed and hashes == after and digest == after_digest
    proof.hashes, proof.bundle_digest = hashes, digest
    proof.exit_code, proof.captured_stdout, proof.captured_stderr = run.exit_code, run.excerpt, ""
    proof.last_run_at, proof.last_run_evidence_path = time.time(), directory
    proof.notes = run.detail if hashes == after else "PoC files changed during execution; re-proof required"
    proof.verified_at = time.time() if proof.verified else None
    finding.status = "exploit_verified" if proof.verified else "proof_attached"
    _status(report)
    return proof


def _patch_paths(report, finding):
    patch = finding.remediation.patch_diff
    paths = []
    for line in patch.splitlines():
        if line.startswith(("--- ", "+++ ")):
            path = line[4:].split("\t", 1)[0]
            if path == "/dev/null":
                continue
            if path.startswith(("a/", "b/")):
                path = path[2:]
            # Never feed a patch with traversal or protected targets to git.
            safe_path(report.workspace_root, path, exists=False)
            if path not in paths:
                paths.append(path)
    affected = {p.rsplit(":", 1)[0] for p in finding.affected_paths}
    if not paths or not affected.intersection(paths):
        raise ValueError("patch must change an affected source file")
    if set(paths).intersection(finding.proof.hashes):
        raise ValueError("patch cannot modify the frozen PoC bundle")
    for path in paths:
        if not any(_within(os.path.join(report.workspace_root, p), os.path.join(report.workspace_root, path))
                   for p in report.scope.get("in_scope_paths", [])):
            raise ValueError("patch target is outside scan scope")
    return paths


def _patch_in_tree(report, finding):
    paths = _patch_paths(report, finding)
    env = scrubbed_env()
    reverse = subprocess.run(["git", "apply", "--reverse", "--check", "--whitespace=nowarn", "-"],
                             input=finding.remediation.patch_diff, cwd=report.workspace_root,
                             env=env, capture_output=True, text=True, timeout=15)
    diff = subprocess.run(["git", "--no-pager", "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "HEAD", "--", *paths],
                          cwd=report.workspace_root, env=env, capture_output=True, text=True, timeout=15)
    def edits(text):
        result = Counter()
        old_path = current = ""
        for line in text.splitlines():
            if line.startswith("--- "):
                old_path = line[4:].split("\t", 1)[0]
            elif line.startswith("+++ "):
                current = line[4:].split("\t", 1)[0]
                if current == "/dev/null":
                    current = old_path
                if current.startswith(("a/", "b/")):
                    current = current[2:]
            elif current and line.startswith(("+", "-")):
                result[(current, line)] += 1
        return result
    wanted = edits(finding.remediation.patch_diff)
    actual = edits(diff.stdout)
    if reverse.returncode or diff.returncode or not wanted or any(actual[k] < v for k, v in wanted.items()):
        raise ValueError("declared patch hunks are not present in the working tree diff against HEAD")
    return paths


def verify_remediation(finding, *, cwd=None, report=None, timeout=60, owner=None):
    writable(owner)
    _editable(finding)
    proof, rem = finding.proof, finding.remediation
    if rem is None or not proof or not proof.verified:
        raise ValueError("a verified proof and attached remediation are required")
    if report is None or owner is None:
        raise ValueError("verification requires a session and active report")
    rem.verified, rem.verified_at = False, None
    finding.status = "remediation_attached"
    hashes, _, digest = proof_bundle(proof, report.workspace_root, owner)
    if not proof.bundle_digest or digest != proof.bundle_digest or hashes != proof.hashes:
        raise ValueError("PoC changed since proof verification; re-proof required")
    if not proof.probe_marker or not rem.regression_checks:
        raise ValueError("remediation requires a probe marker and declared regression checks")
    paths = _patch_in_tree(report, finding)
    regression_hashes = {}
    for command in rem.regression_checks:
        deps, _, _ = proof_bundle(SecurityProof(command=command), report.workspace_root, owner)
        regression_hashes.update(deps)
    baseline = hash_files(paths, report.workspace_root) | regression_hashes
    predicate = {"all": [{"exit": proof.expected_exit, "markers": [proof.probe_marker]},
                           {"markers": proof.expected_markers, "absent": True}]}
    run, _ = _run(owner, report, finding, proof.command, predicate, "remediation", timeout, hashes)
    rem.post_patch_exit_code, rem.post_patch_stdout, rem.post_patch_stderr = run.exit_code, run.excerpt, ""
    checks = []
    if run.passed:
        for command in rem.regression_checks:
            result, _ = _run(owner, report, finding, command, {"exit": 0}, "regression", timeout, hashes)
            checks.append(result)
    after, _, after_digest = proof_bundle(proof, report.workspace_root, owner)
    stable = (hashes == after and digest == after_digest and baseline == hash_files(baseline, report.workspace_root))
    rem.verified = run.passed and len(checks) == len(rem.regression_checks) and all(r.passed for r in checks) and stable
    rem.verified_hashes = baseline if rem.verified else {}
    rem.verified_at = time.time() if rem.verified else None
    rem.notes = "PoC executes without exploit markers; regression checks pass" if rem.verified else (
        "files changed during verification" if not stable else "; ".join([run.detail] + [r.detail for r in checks if not r.passed]))
    finding.status = "remediation_verified" if rem.verified else "remediation_attached"
    patch = safe_path(report.workspace_root, os.path.join(report.directory, "patches", finding.finding_id + ".diff"), exists=False)
    _atomic(patch, rem.patch_diff)
    rem.patch_applied_path = patch
    _status(report)
    return rem


def approve_finding(finding, *, report=None, owner=None):
    _editable(finding)
    if not finding.proof or not finding.proof.verified:
        raise ValueError("cannot approve: proof has not been verified")
    if not finding.remediation or not finding.remediation.verified:
        raise ValueError("cannot approve: remediation has not been verified")
    if not finding.severity_confirmed:
        raise ValueError("confirm CVSS severity against the demonstrated impact before approval")
    if report is None or owner is None:
        raise ValueError("approval requires the report and session for integrity checks")
    writable(owner)
    hashes, _, digest = proof_bundle(finding.proof, report.workspace_root, owner)
    if digest != finding.proof.bundle_digest or hashes != finding.proof.hashes:
        raise ValueError("PoC changed; re-proof required")
    rem = finding.remediation
    _patch_in_tree(report, finding)
    if hash_files(rem.verified_hashes, report.workspace_root) != rem.verified_hashes:
        raise ValueError("patched files changed; verify remediation again")
    finding.status = "approved"
    finding.updated_at = time.time()
    _status(report)
    return finding


def refute_finding(finding, *, reason=""):
    _editable(finding)
    if not reason.strip():
        raise ValueError("refutation requires a reason")
    finding.status, finding.refutation_reason = "refuted", reason.strip()
    finding.updated_at = time.time()
    return finding


_SINKS = ("eval(", "exec(", "pickle.loads(", "shell=True", "shell = True", "innerHTML", "dangerouslySetInnerHTML",
          "yaml.load(", "os.system(", "subprocess.", "SELECT ", "execute(")
_ENTRIES = ("@app.", "@router.", "add_argument(", "@command(", "addEventListener(", "app.get(", "app.post(")


def scan_attack_surface(report, patterns=None):
    if not report.scope:
        raise ValueError("set_security_scope must come first")
    needles = list(patterns or _SINKS)
    if len(needles) > 50 or any(not isinstance(p, str) or not p or len(p) > 200 for p in needles):
        raise ValueError("use at most 50 literal sink patterns, each 1–200 characters")
    previous = {(s["path"], s["line"], s["kind"]): s for s in report.surface}
    discovered = []
    seen = set()
    for raw in report.scope["in_scope_paths"]:
        start = Path(safe_path(report.workspace_root, raw))
        files = [start] if start.is_file() else start.rglob("*")
        for path in files:
            rel = os.path.relpath(path, report.workspace_root)
            if rel in seen or any(p.startswith(".") or p in {"node_modules", "__pycache__"} or p.startswith("security_scan_") for p in Path(rel).parts):
                continue
            seen.add(rel)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            if not _within(report.workspace_root, path) or is_denied_path(str(path))[0]:
                continue
            text = path.read_text(errors="replace")
            if "\0" in text:
                continue
            for line, snippet in enumerate(text.splitlines(), 1):
                for kind, words in (("sink", needles), ("entry", _ENTRIES)):
                    if not any(word in snippet for word in words):
                        continue
                    key = (rel, line, kind)
                    clean = redact_secrets(snippet.strip()[:400])[0]
                    item = previous.get(key)
                    if not item or item["snippet"] != clean:
                        sid = "s" + hashlib.sha256(f"{rel}:{line}:{kind}".encode()).hexdigest()[:12]
                        item = {"id": sid, "kind": kind, "path": rel, "line": line, "snippet": clean, "status": "unreviewed", "note": ""}
                    discovered.append(item)
                    if len(discovered) >= 10000:
                        raise ValueError("surface exceeds 10000 items; narrow the scan scope")
    report.surface = discovered
    report.surface_scanned = True
    _status(report)
    return coverage(report)


def review_surface_item(report, item_id, verdict, note, finding_id=""):
    if verdict not in {"reviewed_clean", "finding", "observation"} or not note.strip():
        raise ValueError("review needs a verdict and note")
    item = next((s for s in report.surface if s["id"] == item_id), None)
    if item is None:
        raise ValueError("unknown surface item")
    if verdict == "finding":
        finding = require_finding(report, finding_id)
        if item_id not in finding.surface_ids:
            finding.surface_ids.append(item_id)
        item["finding_id"] = finding_id
    if verdict == "observation":
        add_observation(report, title=f"{item['path']}:{item['line']}", note=note, surface_ids=[item_id])
    item.update(status=verdict, note=note)
    return item


def add_observation(report, *, title, note, surface_ids=None):
    if not report.scope or not title.strip() or not note.strip():
        raise ValueError("observations need scope, title and note")
    ids = set(surface_ids or [])
    if ids - {s["id"] for s in report.surface}:
        raise ValueError("unknown surface item")
    obs = {"id": f"o{len(report.observations)+1}", "title": title, "note": note, "surface_ids": sorted(ids)}
    report.observations.append(obs)
    for item in report.surface:
        if item["id"] in ids:
            item.update(status="observation", note=note)
    return obs


def coverage(report):
    return {"total": len(report.surface), "reviewed": sum(s["status"] != "unreviewed" for s in report.surface),
            "sinks": sum(s["kind"] == "sink" for s in report.surface),
            "sinks_reviewed": sum(s["kind"] == "sink" and s["status"] != "unreviewed" for s in report.surface)}


def summarize_report(report):
    _status(report)
    return {"scan_id": report.scan_id, "title": report.title, "status": report.status,
            "directory": report.directory, "metadata_path": report.metadata_path,
            "findings_total": len(report.findings), "coverage": coverage(report), "scope": report.scope,
            "by_status": dict(Counter(f.status for f in report.findings)),
            "by_severity": dict(Counter(f.severity for f in report.findings)),
            "approved": [{"finding_id": f.finding_id, "title": f.title, "severity": f.severity}
                         for f in report.findings if f.status == "approved"]}


def generate_report(report):
    _status(report)
    if not report.scope:
        raise ValueError("set scope before generating a report")
    cov = coverage(report)
    lines = [f"# {report.title}", "", f"Scan `{report.scan_id}` · {report.status}", "", "## Scope", ""]
    lines += [f"- **{key.replace('_', ' ').title()}:** {', '.join(val) if isinstance(val, list) else val}"
              for key, val in report.scope.items()]
    lines += ["", "## Coverage", "", f"{cov['reviewed']} of {cov['total']} surface items reviewed; {cov['sinks_reviewed']} of {cov['sinks']} sinks reviewed.",
              "", "Pattern discovery is a review aid, not an exhaustive inventory.", "", "## Approved findings", ""]
    for f in report.findings:
        body = [f"# {f.finding_id}: {f.title}", "", f"{f.cwe} · {f.cvss_score:.1f} {f.severity} · `{f.cvss_vector}`", "",
                f"Status: {f.status}", "", f.summary, "", "## Exploit", "", f.exploit_path, "", f.demonstrated_impact,
                "", "Affected: " + ", ".join(f.affected_paths), "", "Severity rationale: " + f.severity_rationale, "", "## Fix", "",
                f.remediation.description if f.remediation else "No verified fix.", "",
                f"[Patch](../patches/{f.finding_id}.diff)" if f.remediation and f.remediation.patch_applied_path else "",
                "", "## Evidence", ""]
        for run in f.runs:
            body.append(f"- [{run['purpose']} {run['id']}](../{run['evidence_path']}/run.json): {'passed' if run['passed'] else 'failed'}, {run['runner']}, exit {run['exit_code']} — `{run['command']}`")
        path = safe_path(report.workspace_root, os.path.join(report.directory, "findings", f.finding_id + ".md"), exists=False)
        _atomic(path, redact_secrets("\n".join(body) + "\n")[0])
        if f.status == "approved":
            lines += [f"- [{f.finding_id}: {f.title}](findings/{f.finding_id}.md) — {f.cvss_score:.1f} {f.severity}"]
    lines += ["", "## Unsettled hypotheses", ""]
    lines += [f"- [{f.finding_id}: {f.title}](findings/{f.finding_id}.md) — {f.status}" for f in report.findings if f.status not in {"approved", "refuted"}]
    lines += ["", "## Refuted hypotheses", ""]
    lines += [f"- {f.finding_id}: {f.title} — {f.refutation_reason}" for f in report.findings if f.status == "refuted"]
    lines += ["", "## Observations", ""]
    lines += [f"- {o['title']}: {o['note']}" for o in report.observations]
    lines += ["", "CVSS is owned by FIRST and used by permission. [CVSS 3.1 specification](https://www.first.org/cvss/v3.1/specification-document).", ""]
    path = safe_path(report.workspace_root, os.path.join(report.directory, "REPORT.md"), exists=False)
    _atomic(path, redact_secrets("\n".join(lines))[0])
    return path


def capsule(owner):
    report = active_report(owner, required=False)
    if report is None:
        return "No scan active. Create or open a security report, then set its scope."
    c = coverage(report)
    return (f"Scan {report.scan_id}: {report.status}; runner {runner_for(owner, report)}.\n"
            f"Coverage {c['reviewed']}/{c['total']}; {len(report.findings)} findings.\n" +
            "\n".join(f"{f.finding_id}: {f.status} — {f.title}" for f in report.findings if f.status not in {"approved", "refuted"}))
