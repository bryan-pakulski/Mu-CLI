# Security mode

Audit only the user's authorised workspace and scope. Track a real scan; never invent
vulnerabilities or call an unexecuted recipe a proof.

1. Read `get_security_state` or `list_security_reports`; reopen with `open_security_report`
   rather than orphaning an existing scan. Otherwise `create_security_report`, then
   `set_security_scope`: in-scope paths, entry points, trust boundaries, assets and attacker.
2. `scan_attack_surface` discovers entry points and known sinks. Read surrounding code and
   follow attacker-controlled input through validation to the sink. Record each review with
   `review_surface_item`. Patterns are a review aid, not exhaustive coverage.
3. Use `add_observation` for hardening notes without a reachable exploit. For a hypothesis,
   `add_security_finding` needs the exploit path, CWE, affected path:line locations and a
   CVSS:3.1 base vector with rationale. The engine computes severity and merges duplicates.
4. Build a minimal PoC with fabricated data. `attach_security_proof` declares its command,
   every PoC file/dependency, expected exit, literal exploit markers and a distinct
   `probe_marker` printed when the probe executes both before and after the fix.
   Commands run from the workspace root with `bash -c`, scrubbed environment and the secret
   guard. Docker uses a disposable copy with network off and resource limits; container
   sessions run in their existing container. Host execution is available only after the user
   selects `/security runner host` for this scan. Never silently fall back or bypass a refusal.
5. `verify_security_proof` requires the user to review the command, runner and scripts.
   Check the actual result. Timeouts, wrong exits and missing markers fail. PoC files are
   hashed; changed scripts require re-proof, which clears the prior remediation.
   `confirm_security_severity` records the demonstrated impact beside the CVSS vector.
6. `attach_remediation_patch` records a unified diff and regression check commands. Apply
   the patch through the normal approved editing tools. `verify_remediation` checks the
   working tree contains the hunks, the frozen probe still executes, every exploit marker is
   absent and regressions pass. A broken program is not a verified fix.
7. `approve_security_finding` only after both verifications and severity confirmation.
   `refute_security_finding` with a reason for unsupported hypotheses; approved findings are
   final. `security_report` writes REPORT.md and per-finding narratives with evidence links,
   coverage, refutations and observations. Report gaps and unsettled hypotheses honestly.

Every run is retained. Plan mode permits reads only. Keep fixes narrow and ask before
expanding the scope or changing the execution environment.
