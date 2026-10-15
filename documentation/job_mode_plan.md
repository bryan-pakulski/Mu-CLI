# Job mode implementation plan

Status: implemented (2026-10-14); see "Outcome" at the end. Source: the five-lens review of job mode
(git repos, Jira, Docker, autonomy/pipeline, UX). Snapshot of the tree before
this plan: `refs/snapshots/pre-job-plan`.

## Goal

Job mode should be a universal, autonomous engineering tool: point it at any
git repo (local path or URL) and a task (free text or a tracker ticket), and it
plans, implements, verifies, reviews and opens a PR in a disposable container,
with every step visible on terminal, web and mobile and a human only at the
gates that matter (plan approval, push).

## Invariants (never trade these away)

- Approval gates for mutations stay; plan and review stages stay read-only.
  Unattended sandbox writes remain an explicit user choice, never inherited
  silently.
- Credentials never enter job records, logs, or pushed diffs.
- "Done" is decided by deterministic checks, not model self-report.
- A push sends exactly a verified tree.
- Existing tests are not weakened; tests change only where specified
  behaviour changes.

## Decisions taken

| Topic | Decision |
|---|---|
| Default job cost cap | `$100` per job (`jobs.max_cost_usd`), `0` = unlimited, must be set explicitly |
| Commit identity | Inherit the user's `git config user.name/email`; fall back to `MuCLI`. Checkpoints stay unsigned; publish squashes into one commit that honours `commit.gpgsign`, carries the ticket key and a `Mucli-Job: <id>` trailer |
| Push gate after squash | Gate compares the **tree** of the pushed commit with the verified tree |
| Unattended writes | Explicit per-job "Run unattended in sandbox" flag with a Settings default (off). No longer inherits session YOLO. *Superseded 2026-10-15: jobs always run unattended; the flag and setting are gone* |
| State model | One transition table module; stored `status` column kept for compatibility, `JobState` derived by a total explicit mapping (no history heuristics) |
| Trackers | `Tracker` protocol with Jira and GitHub Issues adapters; Linear documented as a plug-in point |
| Service-dependent tests (testcontainers, compose) | Documented limitation plus "run checks inside the job engine" option is **deferred**; doctor explains it |
| Setup vs Doctor | One "Setup" nav entry; Doctor becomes a tab of Setup on every surface |

## Waves and ownership

Agents share one working tree, so each workstream owns a file set. Shared
files edited by more than one workstream are listed with the owner; others
make additive edits only, re-reading before each edit. Each agent runs focused
tests; the integrator runs the full suite (`pytest tests/ -q`, Jest) between
waves.

### Wave 1 — safety, correctness, quick UX wins

**W1 Recovery and scheduling** — owns `mu/jobs/service.py`, `mu/jobs/worker.py`,
`mu/jobs/controller.py`, `mu/jobs/store.py`, new `mu/jobs/sandbox/reaper.py`.
1. Lease expiry in PLANNING recovers to PLANNING (not RECOVERING). `run_job`
   refuses to implement a staged job without `plan_approved_version`
   (job goes to NEEDS_HUMAN with a clear reason). Test reproduces the crash.
2. On any lease recovery, remove the job's non-engine stage containers
   (`label=io.mucli.job=<id>`) before re-dispatch (`reaper.reap_stage_containers`).
   Docker absent → no-op with debug log.
3. Runtime deadline counts active stage time only (sum of RUNNING/PLANNING/
   VERIFYING/REVIEWING/PUBLISHING intervals from status history), not wall time.
4. Scheduler selects QUEUED candidates FIFO (`created_at ASC`, respecting
   `next_run_at`), not `updated_at DESC`.
5. Crash retries of VERIFYING/REVIEWING/PUBLISHING are capped
   (`jobs.max_stage_crash_retries`, default 2) then NEEDS_HUMAN.
6. Recovering a RUNNING job closes the orphaned attempt row (`interrupted`).
7. Retry semantics in `worker.py`: honour `JobRunOutcome.transient` (set by W2)
   with backoff; fix attempts no longer count as retries.

**W2 Sandbox and cost** — owns `mu/jobs/sandbox/{runner,engine,docker_proxy,
policy,worker,harness_worker,model_relay,check_runner,selftest}.py`,
`mu/jobs/job_settings.py`, `mu/jobs/stages.py`.
1. Plan/review/checks stages cannot write the worktree through DinD: either no
   engine for read-only stages or `/workspace` binds rewritten read-only in the
   proxy (`ProxyContext.readonly`). Test via proxy unit tests.
2. Stage cost is always recorded: paused, failed and timed-out runs return the
   relay's accrued cost; plan/review use `cost_add`; `job.cost_usd` is derived
   from `costs.jsonl` (single source). Stage budget is per job-stage across
   resumes/fix attempts (read prior spend from `costs.jsonl`). Default job cap
   `$100`. Unpriced models: warn once and count tokens against a conservative
   fallback price instead of `$0`.
3. Workspace pre-check: skip gitignored/untracked-ignored paths and vendor dirs
   (`node_modules`, `.venv`, `venv`, `target`, `build`, `dist`, `.gradle`,
   `__pycache__`) — those are never mounted secrets the agent didn't already
   produce; symlinks inside ignored trees don't abort. Tracked denied files can
   be allowed via `.mucli/jobs.toml` `[sandbox] allow_paths = [...]` (read from
   the base commit). The same check runs in `_guard` after implement so
   problems surface at the stage that caused them.
4. `_guard` after implement and checks scans `diff.patch` with
   `mu.security.diff_scan.scan_patch(patch, literals=<injected credentials>)`
   and fails the stage on findings. The checks stage gets a `_guard` too.
5. Transient error class: the container reports `error_class`
   (`provider_transient`, `rate_limited`, `iteration_limit`, `fatal`); runner
   sets `JobRunOutcome.transient` for the first two.
6. `pids-limit` configurable (`jobs.sandbox.pids_limit`, default 4096).
7. Error messages: no terminal-only hints ("press E") or module invocations on
   shared surfaces; point to Setup. Runtime image staleness is checked at
   launch (label/digest compare) and surfaced as a warning on the job.

**W3 Git and publish** — owns `mu/jobs/{publish,worktree,base_drift,review,
repository,review_branch,checks}.py`.
1. Re-push after re-prompt or crash updates the existing PR (`metadata.pr` or
   host lookup by head branch) instead of creating a second.
2. PR base = upstream of the job's base branch, falling back to the remote
   default branch; strip `origin/`.
3. Best-effort `git fetch <remote> <base>` (timeout, offline-tolerant) before
   base resolution, drift check and publish; drift compares with the
   remote-tracking ref.
4. Base conflicts: before requeue, the host merges the base tip into the job
   worktree. Clean merge → checkpoint commit and continue; conflicts → commit
   with markers as a checkpoint and pass conflicted files to the agent prompt.
5. Commit identity + squash at publish (see Decisions). Push gate compares
   trees. Repo hooks: still disabled for checkpoints; the final squashed commit
   runs `commit-msg` only if `jobs.publish.run_hooks` is on (default off).
6. Remotes and providers configurable: `jobs.publish.push_remote`,
   `pr_remote`, `provider_hosts` map (host → github|gitlab|bitbucket|gitea).
   Add Bitbucket Cloud and Gitea API adapters next to GitHub/GitLab.
7. Diff secret scan before push (`scan_patch`), refusing with a clear error.
8. Checks: build byproducts (untracked files and lockfile rewrites created by
   suites) are reported as `byproducts`, not a dirty-tree failure; they are
   discarded between suites. Real source edits by checks still fail.
9. Job branches are deleted (local and, if pushed and merged/cancelled,
   remote when `jobs.publish.delete_remote_branch` is on) on archive.
10. Large repos: configurable git timeouts (`jobs.git_timeout_seconds`),
    `TimeoutExpired` wrapped as `RepositoryError`.

**W4 Trackers** — owns `mu/integrations/*`, new `mu/jobs/tracker_sync.py`, the
tracker endpoints in `mu/gui/routers/jobs.py`, `mu/gui/static/js/work_jira.js`,
the Jira step in `mu/jobs/setup.py`.
1. `Tracker` protocol (`fetch`, `search`, `to_payload`, `comment`,
   `transition`, `link_pr`) with Jira and GitHub Issues adapters; jobs carry
   `metadata.ticket = {tracker, key, url}` (keep `metadata.jira` as alias).
2. Writeback (`tracker_sync.notify(service, job, event, **info)`): events
   `created`, `plan_ready`, `pushed` (PR URL), `done`, `failed`,
   `needs_human`; comments plus configurable status transitions
   (status name → transition lookup). Settings in `jira.json`, editable in
   GUI/wizard. Best-effort, never fails a job.
3. JQL/board imports fetch full issue fields; per-key errors are collected,
   not batch-fatal; bounded retry honouring `Retry-After` on 429/5xx.
4. Duplicate protection: indexed `(tracker, key)` lookup over live jobs under a
   lock; DONE tickets skipped unless forced.
5. Blocking HTTP moved off the event loop (`run_in_threadpool`).
6. Deployment detection (`/serverInfo` → cloud|dc) picks API version; PAT
   prompt in terminal wizard; ADF mapping for mentions, links, code fences,
   lists, tables; configurable acceptance-criteria custom field; attachment
   metadata in `ticket.json`.
7. Optional watcher `tracker_sync.poll_watches(service)`: configured JQL /
   GitHub label queries imported on an interval (off by default).
8. Ticket text is framed as untrusted in stage prompts.
9. Fix the wizard text to match real behaviour.

**W5 Terminal and docs** — owns `mu/commands/job.py`, `documentation/*`
(except this file), `README.md`, `mu/ui/jobs/*`.
1. `/job approve <id>` defaults to the latest plan version.
2. One-shot `/job <TICKET-KEY|title> [--in PATH]`; `--in` works without a chat
   session (hidden session created automatically).
3. Lowercase aliases only in help (`approve-plan` etc.; camelCase still
   accepted).
4. Terminal dashboard: Doctor as a tab of Setup; Toolchains folded into
   Environments; tab names aligned with web/mobile.
5. Docs: one-page quickstart (golden path), milestone history moved to
   `documentation/archive/`, contradictions fixed, README section on jobs.

**W6 Web and mobile UX** — owns `mu/gui/templates/work.html`,
`mu/gui/static/js/work*.js`, `mu/gui/static/js/work/*`, `mu/gui/static/css/work.css`,
`mobile/android/src/**` (except tracker sheets owned by W4).
1. New job: workspace path first (recent workspaces, current session
   preselected), single "Title or ticket key" field; models, sandbox, retry
   and retention under Advanced with one per-stage model picker.
2. Fix contradicting copy (validation commands, review report-only, blank
   stage models, Jira credentials, Android tabs).
3. Doctor as a tab of Setup; one Setup call-to-action banner; Boards renamed
   "Tickets"; nav trimmed.
4. "Run unattended in sandbox" toggle (explicit, default from Settings, off). *Removed 2026-10-15: jobs always run unattended.*

### Wave 2 — simplify the core (single agent, after Wave 1 is green)

**W7 Architecture cleanup** — owns all of `mu/jobs/`.
1. Delete unreachable legacy pipeline: `apply_verification_result` tail,
   `DeterministicVerifier.verify`, `acceptance.py`, `materialize_review_branch`,
   in-process `SessionJobRunner.run`, `_base_args`, legacy verdict mapping;
   remove their tests only where the behaviour no longer exists.
2. One transition table (`mu/jobs/core/states.py`) used by store, service,
   controller; `evolve_legacy` heuristics replaced by an explicit mapping;
   unreachable `JobStatus` values dropped with a store migration.
3. One worker entry point dispatching by stage; shared heartbeat/lease/release
   helper; all state writes lease-fenced in the same transaction.

### Wave 3 — universal reach and visibility

**W8 Repo intake** — `service.py`, `repository.py`, new `mu/jobs/clone.py`.
Repo URL (`https`/`ssh`, `@branch`) cloned into `~/.mucli/repos/<hash>` with
host credential helpers, `--filter=blob:none`, optional submodules and LFS;
jobs accept `repository` again (URL or path); submodules initialised in job
worktrees; the sandbox mounts the job worktree's gitdir and common objects
read-only so VCS-aware tools work.

**W9 Universal sandbox** — `mu/jobs/sandbox/*`, `checks.py`, `dynamic_checks.py`.
Per-repo `.mucli/jobs.toml` keys: `[sandbox] allow_paths, egress,
validation_allowlist, image`; toolchain detection from manifests (go.mod,
Cargo.toml, pom.xml, build.gradle, pyproject/uv/poetry, package.json/bun);
devcontainer `image`/`build.dockerfile` as an environment source (approval
required); shared per-repo-per-toolchain dependency cache keyed by lockfile
hash; engine API over a bind-mounted Unix socket (rootless Docker / Docker
Desktop); `jobs.container_cli` (docker|podman) with doctor detection;
SELinux `:z` labels; doctor measures Docker's data root.

**W10 Visibility** — controller, publish, routers, web, mobile, terminal.
Live diff endpoint while a job is active (host-side `git diff` against base)
on all surfaces; PR merge detection (poll via `gh`/API) → MERGED; Android
local notification on "needs you"; tracker watcher and writeback wired into
the controller.

## Integration steps (integrator)

After Wave 1: wire `tracker_sync.notify` into state transitions and publish,
`poll_watches` into the controller tick; reconcile shared files; full suite +
Jest + `node --check`; Playwright screenshot of New job / Setup on a spare
port. Same after Waves 2 and 3. Rebuild the Android APK at the end.

## Verification

- Full Python suite and Jest green after every wave.
- New regression tests for every bug fixed in Wave 1.
- Docker-backed tests (`tests/test_job_harness_docker.py`) run where Docker is
  available.

## Outcome

All three waves landed, followed by an independent security, pipeline and
cross-surface review whose findings were fixed with regression tests.
Snapshots: `refs/snapshots/pre-job-plan`, `refs/snapshots/wave1`,
`refs/snapshots/wave2`.

Verification at completion: Python suite 4061 passed / 9 skipped; Docker
sandbox tests 7/7; Jest 220/220; `tsc --noEmit` and `node --check` clean;
settings table checked by `python3 -m mu.jobs.settings_doc --check`.
Android 0.4.0 (versionCode 13) adds `expo-notifications`.

Deviations from the plan:

- Job cost cap uses the existing `defaults.max_cost_usd` key (default 100)
  rather than a new `jobs.max_cost_usd`; publish settings live under
  `publish.*`.
- Closed-unmerged PRs park as NEEDS_HUMAN (`pr_closed`) with Re-push / Mark
  done actions.
- Sandbox git access uses a per-job shallow object store holding only the job
  HEAD (no other branches, stash or history), not the repository common dir.
- Repo-declared egress and cloned-repo `environments` require approval;
  unapproved egress is simply not applied (non-blocking banner).

Still deferred:

- Tests that need service containers/ports (testcontainers, compose port
  mapping) from stage containers; doctor explains the limitation.
- devcontainer features and lifecycle commands; compose-based devcontainers.
- Git inside submodules in the sandbox; LFS objects via alternates.
- Sysbox DinD on Docker Desktop and Podman (stage containers work).
- Linear tracker adapter (the `Tracker` protocol is the plug-in point).
- Android notifications only while the app is running (no push service).
- Run again from the New job sheet's ticket-key import.
