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
