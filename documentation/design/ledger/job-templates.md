# Test migration ledger: job templates (run a job in a container template)

Format (spec §8.3): `file:line — old → new — intent`. Every rewritten
assertion asserts the equivalent of the same intent under the specified
behaviour change (`documentation/plans/job_templates.md`); nothing was deleted
without a replacement and nothing was skipped.

## Vocabulary

- `tests/test_jobs_tui.py:644` — status glyph table `{done, active, waiting, failed, skipped, pending}` → the same table plus `"unverified": "?"` — the terminal status table equals the shared vocabulary, which gains the `unverified` status (job_templates.md §5: glyph `?`, semantic `caution`).

## Web: New job sheet "Run in"

- `tests/test_job_new_sheet_workspace.py:93-95` — `id="job-environments-field"` asserted inside the Advanced section → asserted between the workspace field and the Advanced section, containing the `id="job-run-in"` picker labelled "Run in" with an "Advanced…" action; agents, retries and cost stay inside Advanced — the sheet still has one environment control, now the visible "Run in" picker that replaces the Build environments summary row (job_templates.md §3.2, "Change becomes Advanced…").

## Coordinator follow-up (merge into wash/overhaul)

- tests/test_job_templates.py:233 — seeded this repository's row with `paths: "web"` and expected remember to drop it → seeded a whole-repository row (`paths: ""`) and expect it replaced — intent unchanged: remember writes the repository's whole-repo Toolchains row. The known gap "remember discards per-component rows" is fixed: `remember_template` now keeps rows with `paths`, covered by the new `test_remember_keeps_per_component_toolchain_rows`.

## Default package-registry egress (2026-10-15)

`sandbox.egress_extra` now defaults to the npm, Cargo/crates.io, Maven/Gradle and PyPI registries (`mu.container.network.PACKAGE_REGISTRY_EGRESS`, also part of `DEFAULT_EGRESS_ALLOW`), and `SandboxPolicy.for_stage` reads the effective rule instead of the raw saved value. Tests that assumed an empty default now pin `egress_extra: []` to keep testing their original intent:

- tests/test_job_settings_cli.py:211 — TUI list edit typed into an empty field → sets `egress_extra` to `[]` first, since the editor pre-fills the (now non-empty) default — intent: comma-separated list parsing.
- tests/test_job_toolchains.py:86 — `for_stage("checks")` → `for_stage("checks", {"sandbox": {"egress_extra": []}})` — intent: environment egress is inherited without model hosts.
- tests/test_job_templates.py:184 — fit computed with the default extra egress → with `egress_extra` cleared — intent: a template lacking npm is flagged.
- tests/test_job_templates.py:412 — `run_job_setup(..., settings={})` → `settings={"sandbox": {"egress_extra": []}}` — intent: setup uses the template allowlist minus model hosts.
- New: tests/test_default_package_registry_egress.py covers the defaults, plan/review staying closed, an explicit `[]` locking jobs down, and fit not warning for default registries.
