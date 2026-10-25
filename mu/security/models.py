"""Persisted scan records; unknown fields are ignored on load."""
from __future__ import annotations
import time
from dataclasses import dataclass, field


@dataclass
class SecurityProof:
    """Executable recipe that demonstrates a vulnerability."""

    files: list[str] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    probe_marker: str = ""
    expected_exit: int = 0
    bundle_digest: str = ""
    kind: str = "command"           # "command" | "script" | "http"
    command: str = ""               # shell command run from workspace root
    expected_markers: list[str] = field(default_factory=list)
    description: str = ""
    captured_stdout: str = ""
    captured_stderr: str = ""
    exit_code: int | None = None
    verified: bool = False
    verified_at: float | None = None
    last_run_at: float | None = None
    last_run_evidence_path: str = ""
    notes: str = ""


@dataclass
class SecurityRemediation:
    """Proposed fix + post-patch re-verification."""

    regression_checks: list[str] = field(default_factory=list)
    verified_hashes: dict[str, str] = field(default_factory=dict)
    description: str = ""
    patch_diff: str = ""             # unified diff the user should apply
    patch_applied_path: str = ""     # where the diff is stored on disk
    post_patch_stdout: str = ""
    post_patch_stderr: str = ""
    post_patch_exit_code: int | None = None
    verified: bool = False
    verified_at: float | None = None
    notes: str = ""


@dataclass
class SecurityFinding:
    finding_id: str
    title: str
    summary: str = ""
    cwe: str = ""
    cvss_vector: str = ""
    cvss_score: float = 0.0
    severity_rationale: str = ""
    demonstrated_impact: str = ""
    severity_confirmed: bool = False
    surface_ids: list[str] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    refutation_reason: str = ""
    vulnerability_class: str = ""    # sql_injection, xss, path_traversal, etc.
    severity: str = "medium"
    affected_paths: list[str] = field(default_factory=list)
    exploit_path: str = ""           # human-readable trace
    references: list[str] = field(default_factory=list)
    status: str = "new"
    proof: SecurityProof | None = None
    remediation: SecurityRemediation | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class SecurityReport:
    scan_id: str
    title: str
    summary: str = ""
    workspace_root: str = ""
    directory: str = ""              # documentation/security_scan_<scan_id>
    metadata_path: str = ""          # absolute path to report.json
    status: str = "scanning"         # scanning | verifying | complete
    scope: dict = field(default_factory=dict)
    surface_scanned: bool = False
    surface: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    findings: list[SecurityFinding] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ---------------- lookup helpers
    def find(self, finding_id: str) -> SecurityFinding | None:
        for f in self.findings:
            if f.finding_id == finding_id:
                return f
        return None
