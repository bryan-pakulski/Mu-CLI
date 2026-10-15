# Test migration ledger: jobs run unattended

Format (spec §8.3): `file:line — old → new — intent`. The specified behaviour
changed by user decision (2026-10-15): "Jobs need to run without approvals. There
is no block on jobs requesting permissions, all jobs run fully automated during
their relevant stages and shouldn't be prompting the user." Every rewritten
assertion covers the same intent under that rule; nothing was deleted without a
replacement and nothing was skipped. New coverage is in
`tests/test_jobs_unattended.py` (29 tests) and one Android jest case.

## Tool approvals and questions

- tests/test_job_review.py:76 — second `request_tool_approval` after the recorded approval was consumed raised `InteractionRequired` → returns `("y", None)` and records one `auto_permission` event; the consumed count is still 1 — intent: a recorded human decision is consumed exactly once, and a stage never turns an approval into a human gate.
- tests/test_job_migration_final.py:98 — `resume` disabled while a tool approval is pending → enabled (Resume resolves the parked approval automatically) — intent: the action availability matches what Resume does.
- tests/test_job_migration_final.py:104-111 — `claim_stage_response` returned `None` for other args, for the review stage and after consumption → returns the automatic response (`auto`: approve in implement, deny in review), and the human decision for the exact args is still returned once without `auto` — intent: a stored approval is consumed once and only for the exact call; nothing else waits on a human.
- tests/test_job_harness.py:168-183 — implement `claude_args(auto_approve=False)` used `--permission-mode manual` without `--dangerously-skip-permissions`; `auto_approve=True` had no `--disallowed-tools` → every stage skips permissions, read-only stages still drop Edit/Write/NotebookEdit, every stage drops `AskUserQuestion`, and the stage system prompt says the stage runs unattended — intent: the CLI flags implement the stage's approval policy.
- tests/test_job_harness.py:186-194 — gated Codex implement used `--approve-for-me` → every stage uses `--dangerously-bypass-approvals-and-sandbox` (the job container is the control) — intent: same.
- tests/test_job_harness.py:146-148 — Codex status warned about "automatic approval review" → that warning is gone — intent: Settings shows only caveats that still apply.
- tests/test_job_harness_docker.py:31-52, 121 — `test_claude_stage_parks_for_approval_and_resumes_its_session` (fake CLI asked for Write permission, the stage parked, a human approved, the session resumed) → `test_claude_stage_decides_permissions_automatically_and_never_parks`: the fake asks for Write in plan (denied, no file) and in implement (allowed, file written in one run), an `auto_permission` approve event is recorded, the CLI session and state are still kept — intent: real-container harness permission handling end to end. Session resume of a paused stage stays covered by `tests/test_job_stage_resume_docker.py`.
- tests/test_job_sandbox_docker.py:86-92 — mucli implement parked on `write_file`, then completed after Approve tool → completes in one run with the write applied only in the job worktree — intent: real-container mucli implement writes, and the host repository is untouched.
- tests/test_web_agent_harness_ui.py:20-22 — New job sheet carried `data-codex-warning` tied to the auto-approve switch → no gated-Codex warning and no `autoApprove` dependency — intent: the sheet warns only about real limitations.

## No per-job auto-approve

- tests/test_job_new_sheet_workspace.py:1-2 — module docstring described the explicit flag → describes the always-unattended rule.
- tests/test_job_new_sheet_workspace.py:52, 63 — `auto_approve_writes is False` → `is True` — intent: the session's YOLO never decides a job's behaviour; jobs are always unattended.
- tests/test_job_new_sheet_workspace.py:66-76 — `test_run_unattended_follows_the_settings_default_and_the_explicit_flag` → `test_jobs_always_run_unattended_and_ignore_an_auto_approve_false_input`: the setting is gone and `auto_approve_writes: false` on input is stored as true — intent: how the unattended policy is set.
- tests/test_job_new_sheet_workspace.py:79-86 — `session_defaults` set `auto_approve_writes: False` → the defaults carry no flag (with or without YOLO) and normalise to true — intent: terminal job defaults do not inherit YOLO.
- tests/test_job_new_sheet_workspace.py:96-97, 109 — the sheet had the "Run unattended in sandbox" switch and `work.js` read `defaults.run_unattended` → neither exists, and `work.js` sends no `auto_approve_writes` — intent: the web sheet's unattended control (retired by this decision).
- tests/test_job_sessions_cli.py:122-123 — `auto_approve_writes is False` → `is True` — intent: `/job create --session` does not inherit YOLO.
- tests/test_job_sessions_plane.py:62-63 — same change for a job created from a saved session that is not loaded — intent: same.
- mobile/android/__tests__/job-sessions.test.tsx:85-86 — `unattendedDefault()` followed `defaults.run_unattended` → removed with the function; covered by the new "offers no unattended / auto-approve control" case — intent: the Android unattended control (retired by this decision).
- mobile/android/__tests__/job-sessions.test.tsx:106-107 — create `execution` equalled `{auto_approve_writes: false}` → equals `{}` — intent: the create payload carries no auto-approve flag.
- mobile/android/__tests__/job-sessions.test.tsx:125-136 — "takes the unattended toggle default from Settings and sends the explicit choice" → "offers no unattended / auto-approve control: jobs always run unattended" (no `job-unattended` control or matching text with Advanced open, no flag in the payload) — intent: same.
- mobile/android/__tests__/job-sessions.test.tsx:148-149 — import body matched `{workspace_root, execution: {auto_approve_writes: false}}` → matches `{workspace_root}` and `execution` has no `auto_approve_writes` — intent: ticket import carries no auto-approve flag.
- New: mobile/android/__tests__/job-visibility.test.tsx — a failed job setup shows as a warning banner (`job-setup-warning`) while the job keeps running.

## Template parks (job_templates.md §4, §5)

- tests/test_job_templates.py:446-479 — `test_setup_failure_parks_with_command_and_log_tail_then_skip` (expected `EnvironmentBlocked` with `.setup_failure`, then a manual park and **Continue without setup**) → `test_setup_failure_warns_with_command_and_log_tail_and_continues`: no exception, the job stays running, `setup_warning` (VM `setup_warning`) carries the command, index, message and E403 tail, one `environment_setup_failed` event, the setup is not re-run for the same image; the legacy park → **Continue without setup** path is kept and clears the warning — intent: a failed setup is reported with the command and log tail, and the user can get past it.
- tests/test_job_missing_toolchain.py:1-3, 33-52 — `test_missing_tool_parks_the_job_without_a_fix_loop_or_baseline_rerun` (exit 22, NEEDS_HUMAN, message in `attention_detail`) → `test_missing_tool_continues_unverified_without_a_fix_loop_or_baseline_rerun` (exit 0, REVIEWING, the same message on the `checks_unverified` event with `verified: false`, still no fix attempt and no baseline re-run) — intent: a missing tool is reported clearly and never starts a repair loop.
- tests/test_job_templates.py:633-675 — `test_unverified_run_parks_with_message_then_continue_caps_review` → `test_unverified_run_continues_to_review_with_the_message_and_caps_the_verdict`: exit 0 and REVIEWING, the same message on the event, `continueUnverified` disabled (nothing parked), the review prompt says UNVERIFIED with the causes; the legacy park → **Continue unverified** path is kept; the verdict is still capped at needs_work with readiness ≤ 60 — intent: unverified checks are honest and cap the review.
- tests/test_job_templates.py:683-722 — `test_push_of_unverified_checks_requires_an_override` (push rejected without `override_reason`, `unverified_override` recorded) → `test_push_of_unverified_checks_needs_no_override_and_the_pr_says_so`: push enabled without `requires_override`, still flagged `unverified`; the push preview body and the published PR body (an edited body too) start with "⚠ Checks could not run (unverified): …"; no override is recorded — intent: unverified changes are never presented as verified when published.

## Repository trust parks

- tests/test_job_sandbox_w9.py:134-163 — `_require_approval` raised `EnvironmentBlocked` with `.approval` for a cloned repository's unapproved `environments`, and passed once approved → `repository_approval` returns the same request (kind, fingerprint, ref, message), an unattended resolution (`resolve_bindings(..., skipped=[])`) skips it and falls back to the default runtime, and after approval it is usable — intent: a cloned repository's environments are never used without the user's approval.
- tests/test_job_sandbox_w9.py:289-303 — `test_explicit_repository_image_parks_the_job_until_approved` → `test_explicit_repository_image_is_skipped_until_approved` (same approval request, skipped with origin `repo-image`, usable after approval; a job override is still exempt) — intent: same, for an explicit `[sandbox] image`.
- New: tests/test_jobs_unattended.py covers the `repository_environment_skipped` event, the non-blocking approval card, pinning for the rest of the job, and the secret-scan fallback (`build_environment_skipped`, the image never used, not retried).
