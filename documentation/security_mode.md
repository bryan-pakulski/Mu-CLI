# Security mode

Switch with `/mode security`. An audit records scope, reviewed attack surface, hypotheses,
executed proofs, fixes, regression checks and observations. The active scan belongs to the
session and reopens after a restart; sharing a workspace does not silently select another
session's scan.

## Start and resume

```text
/security scan API audit
/security status
/security list
/security open <scan_id>
/security scope
/security coverage
/security report
```

Ask the agent to set the scope before reviewing: in-scope paths, entry points, trust boundaries,
assets and attacker. `/security scope <JSON>` also accepts these fields directly. Scope is
frozen once surface review or findings exist; start another scan to expand it.

Scans live in `documentation/security_scan_<id>/`. Session mode state stores the active scan
pointer and revision. `open_security_report` and `list_security_reports` provide explicit
agent access. The browser API always resolves the requested session.

## Host and container execution

Both are supported. On a host workspace, Docker is the default: the `mucli-job-runtime:latest`
image runs against a disposable copy with network disabled, dropped capabilities, no new
privileges, CPU/memory/process limits and a timeout. VCS metadata and protected files are
excluded. Use `mucli jobs setup` to prepare the runtime image.

The user may explicitly choose host execution **for the current scan**:

```text
/security runner host
/security runner docker
/security runner auto
```

The Risk tab also exposes this choice. `auto` selects Docker and revokes host consent; there
is no silent fallback when Docker is unavailable. Host runs can change the real workspace and
use the host network. The host choice is session-owned, persists across restart, and does not
carry into another scan or another session that opens the same scan. It is not a model tool.
For container sessions, commands execute in the existing session container under that
container's filesystem and network policy.

Every PoC and remediation execution requires human approval, including under YOLO. The
preview shows the runner, working directory, command, PoC file contents and hashes, and the
patch and regression commands for remediation. Changed preview inputs require another
approval. Mobile can approve/refute findings, but PoC execution approval requires desktop
command review; it can still cancel a pending run.

The shared `run_check` runner uses `bash -c`, removes credential-shaped environment variables
and shell startup/loader hooks, applies the bash secret guard, redacts output, and bounds run
time. The guard checks directly invoked scripts as well as the command. This is a static
check, not a sandbox for host code: use fabricated data, declare every PoC dependency and
review scripts before running them. Commands should use workspace-relative paths; Docker's
workspace root is `/workspace`.

## Audit workflow

| Tool | Contract |
| --- | --- |
| `create_security_report`, `open_security_report`, `list_security_reports` | Create, select or discover scans. |
| `set_security_scope` | Record paths and threat model before review. |
| `scan_attack_surface` | Discover entry points and common sinks; optional patterns are literal substrings. |
| `review_surface_item` | Record reviewed-clean, finding or observation with a note. |
| `add_observation` | Record hardening advice without claiming an exploit. |
| `add_security_finding` | Require exploit path, CWE, valid affected `path:line` locations, CVSS 3.1 base vector and rationale. Reuse duplicate CWE/sink locations. |
| `attach_security_proof` | Declare command, expected exit, exploit markers, a distinct probe-ran marker and all PoC files/dependencies. |
| `verify_security_proof` | Execute the probe; expected exit and all markers must match. Timeouts and self-modifying PoCs fail. |
| `confirm_security_severity` | Reconfirm the vector against the proof's demonstrated impact. |
| `attach_remediation_patch` | Attach a unified diff and regression check commands after a verified proof. Apply it separately with the usual editing tools. |
| `verify_remediation` | Check patch hunks against the working-tree diff from HEAD; frozen PoC must execute, every exploit marker must be absent, and all regressions must pass. |
| `approve_security_finding` | Require both verifications and severity confirmation; recheck PoC and patched-file integrity. |
| `refute_security_finding` | Record why an unapproved hypothesis was rejected. Approved/refuted records are final. |
| `get_security_state`, `security_report` | Inspect the ledger or generate its Markdown report. |

CVSS scores are computed from the eight base metrics rather than an independently chosen
severity. CVSS is owned by FIRST and used by permission; see the
[CVSS 3.1 specification](https://www.first.org/cvss/v3.1/specification-document).

Surface discovery is not exhaustive. Read surrounding code and follow attacker-controlled
input across trust boundaries. Changed surface snippets lose their old reviewed mark. The
report shows coverage gaps and unsettled hypotheses separately from approved findings.

Reattaching or rerunning a proof clears the old remediation and severity confirmation.
Changing a frozen PoC forces re-proof. A patch that merely crashes the program, removes the
probe marker, leaves even one exploit marker, or fails regressions is not a verified fix.
Patch verification requires a Git workspace with a HEAD commit and the proposed changes
still represented in the working-tree/index diff.

## Evidence and surfaces

Every run has its own directory under `proofs/<finding_id>/<run>/`, with redacted output,
`CheckRun` metadata and a hashed script bundle. Earlier runs are retained. The shared result
store also keeps full output; context excerpts stay bounded. Patches live in `patches/`.
`security_report` writes `REPORT.md` and `findings/<id>.md`, with scope, coverage, approved
findings, refuted hypotheses, observations and evidence links. Scan status advances from
scanning through verifying to complete when findings are settled and discovered items reviewed.

The web panel has Risk, Findings, Coverage and Report tabs, finding sheets, run history and
expanded report/diff views. Android shows findings, CVSS, coverage, observations and run
sheets; decisions use the same engine gates. All security writes and command runs are blocked
in plan mode; state and discovery reads remain available.

Existing prompt overrides remain user-owned. If an override still describes the old workflow,
review it with the prompt settings before resetting it to the bundled security template.
See [security_controls.md](security_controls.md) for always-on harness protections.
