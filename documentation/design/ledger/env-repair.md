# Test migration ledger: environment repair

Format (spec §8.3): `file:line — old → new — intent`. The specified behaviour
changed by user request (2026-10-16, `documentation/plans/job_templates.md` §7):
"If we do start failing checks because the environment is missing dependencies
etc., we can allow the agent to fix the environment." Checks that could not run
now get an unattended repair stage and a re-run before the run is recorded
unverified, and jobs never park for missing tools or dependencies.

## Existing tests

No existing assertion was rewritten, weakened, skipped or deleted. The tests that
pin the unverified path (`tests/test_job_missing_toolchain.py`,
`tests/test_job_templates.py` honest-gate tests, `tests/test_job_checks_stage.py`
fix-loop tests) call `apply_checks_result` directly; repair runs before it, in
`verify_worker._verify`, and only for a prepared binding (or the shared runtime)
of a job whose checks could not run, so those tests keep their exact assertions.

## Behaviour change without a prior test

- `mu/jobs/verify_worker.py` `apply_checks_result` — a run whose new failures were
  all environment symptoms (`futile_fix_reason` "environment: …") parked the job
  (`verification_required`, exit 22); no test covered the park → the job records
  `checks_unverified` and continues to review (exit 0) — intent: jobs never park for
  missing dependencies (§7). Covered by
  `tests/test_job_env_repair.py::test_environment_symptom_failures_continue_unverified_instead_of_parking`.

## New coverage

- `tests/test_job_env_repair.py` (25 tests): trigger on unrunnable checks; missing
  top-level tools route into repair instead of parking; environment-symptom
  failures continue unverified; repair container docker args and isolation; egress
  composition (auto vs template, model hosts dropped); tracked-file revert that
  never touches the implement stage's work; image commit, binding update, setup
  record carried over and later checks using the new image; shared-runtime repair
  and its cleanup; failure, timeout (with the ownership fallback) and budget
  exhaustion continue unverified; a secret-scan hit discards the image; max
  attempts; repair cost in the job cost; prompt contents and command parsing;
  `addRepairToTemplate` (including validation for non-template sources) and
  `saveRepairedTemplate`; settings schema; `repair` as a writing stage; vocabulary
  `repair_outcome`; web, terminal and change-stream contracts.
- `tests/test_job_env_repair_docker.py` (1 test, needs Docker and the local
  `mucli-job-runtime` image): a tiny Node repository whose `npx vitest run` cannot
  run without `node_modules` is repaired by a stub Claude Code harness running
  `npm ci`; the re-run checks pass in the repaired image, which job cleanup removes.
- `mobile/android/__tests__/job-env-repair.test.tsx` (4 tests): the Checks tab
  repair summary and the vocabulary mapping.
