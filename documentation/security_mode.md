# Security Mode

A security audit workflow gated on demonstrable evidence. Switch in via
`/mode security`.

The hard contract: a finding is a hypothesis until its proof-of-concept
executes and the declared markers literally appear in the output. A
remediation is proposed until the same PoC is re-run against the
patched code and the markers no longer appear. No "potentially
vulnerable" findings get approved.

For non-security debugging use [debug](debug_mode.md). For multi-week
hardening campaigns put security mode inside [loop](loop_mode.md).

## Engine tools

The security engine is the only source of truth for the audit. The
audit trail records every hypothesis — including refuted ones.

| Tool | Role |
| --- | --- |
| `create_security_report` | Open the audit. Sets up `documentation/security_scan_<id>/`. |
| `add_security_finding` | Record a hypothesis: title, vulnerability class, severity, affected paths, exploit path. |
| `attach_security_proof` | Attach a shell PoC + `expected_markers` that uniquely identify success. |
| `verify_security_proof` | Engine runs the PoC and verifies markers appear literally. |
| `attach_remediation_patch` | Attach the fix as a unified diff + a description of the defensive principle. |
| `verify_remediation` | Engine re-runs the same PoC against the patched code; markers must no longer appear. |
| `approve_security_finding` | Finalize. Refuses unless both verifications passed. |
| `refute_security_finding` | Abandon a failed hypothesis. Recorded in the appendix. |
| `get_security_state` | Snapshot of the audit: counts by severity, approved vs refuted. |

## Phases

### Phase 1 — Discovery

1. `create_security_report` with a clear title (e.g. "Initial audit of
   acme-api").
2. Scan in parallel:
   - `retrieve_relevant_context` for queries like `authentication`,
     `deserialization`, `SQL queries`, `user input handlers`,
     `command construction`, `secrets`.
   - `search_for_string` for known-bad patterns: `eval(`, `exec(`,
     `subprocess.*shell=True`, `pickle.loads(`, `os.system(`,
     `SELECT.*\+`, `innerHTML.*=`, `request.args`, `request.form`,
     hardcoded credentials.
   - `read_file` candidates fully — bugs are usually three calls away
     from the suspicious line.
3. For each plausible vulnerability, `add_security_finding` with
   title, `vulnerability_class`, severity
   (`info | low | medium | high | critical`), `affected_paths`, and a
   concrete `exploit_path` describing how an attacker triggers it.

### Phase 2 — Per-finding proof-and-patch loop

Run for **every** finding.

1. **Build the PoC.** `attach_security_proof` with a shell command
   that reproduces the vulnerability deterministically from the
   workspace root. Declare `expected_markers` — unique strings that
   appear only when the exploit succeeded (e.g. `PWNED`, a file path
   that shouldn't exist, a stolen secret).
2. **Verify the PoC.** `verify_security_proof`. The engine runs the
   command and confirms the markers literally appear.
   - If false: revise the PoC and retry.
   - After 2–3 failed revisions: `refute_security_finding` with a
     reason. Don't silently drop it — the audit trail must show the
     failed hypothesis.
3. **Engineer the patch.** Read the file, write the corrected code.
   `attach_remediation_patch` with the unified diff and a description
   of the defensive principle (parameterized queries / context-aware
   escaping / safe deserializer / input validation). Apply the patch
   to the working tree via `apply_diff`.
4. **Verify the patch.** `verify_remediation`. The engine re-runs the
   same PoC against the now-patched code; the exploit must no longer
   trigger.
   - If false: the patch doesn't actually fix the vulnerability.
     Revise.
5. **Approve.** `approve_security_finding` only when both
   verifications passed. Then move to the next finding.

### Phase 3 — Final report

- `get_security_state` for the summary: total findings, severity
  breakdown, approved vs refuted.
- Surface every approved finding with a one-paragraph "exploit → fix"
  narrative pointing at the persisted proof + patch artifacts under
  `documentation/security_scan_<id>/`.
- Findings that didn't pass PoC verification go in a "refuted
  hypotheses" appendix — show the work.

## Operating principles

- **Real exploits only.** "Could potentially be vulnerable" is not a
  finding. If you can't write a PoC that triggers, it's a code-quality
  observation — file it separately.
- **Read full files.** Don't reason about snippets.
- **Reason about trust boundaries.** The same code is safe inside a
  process and unsafe at the HTTP edge. Identify where untrusted input
  enters and trace it through.
- **Memory discipline.** `save_memory` durable findings about the
  codebase ("this project uses parameterized queries consistently
  except in `legacy/`"). Future scans benefit.
- **Don't patch what you can't exploit.** Approved findings = verified
  attacks + verified defenses. Anything else is noise.

## Severity guidance

| Severity | When |
| --- | --- |
| `critical` | Remote unauthenticated RCE; full credential exfiltration; auth bypass with privilege escalation. |
| `high` | Authenticated RCE; data exfiltration; auth bypass without escalation; persistent XSS on auth surfaces. |
| `medium` | Stored / reflected XSS without auth bypass; SSRF to internal services; CSRF on sensitive actions. |
| `low` | Information disclosure with no direct exploit chain; weak crypto in non-secret contexts. |
| `info` | Hardening recommendations that aren't exploitable as-is. |

If you'd refute the PoC at this severity ("nobody can actually trigger
this in production"), downgrade or refute.

## Output artifacts

Everything lives under `documentation/security_scan_<id>/`:

- `report.json` — engine state (findings, proofs, patches, decisions).
- `findings/<id>.md` — per-finding write-up with the exploit narrative.
- `proofs/<id>.sh` — the PoC shell command.
- `patches/<id>.diff` — the applied remediation.

These are the deliverables. The conversation transcript is incidental;
the directory is the audit.
