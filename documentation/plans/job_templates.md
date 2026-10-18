# Plan: run a job in a container template

Status: implemented (2026-10-15; see §7) · Owner: jobs · Builds on: `job_build_environments.md`
(rev 3; templates are already valid job environments). Supersedes the Prepare stage and
environment agent in `job_build_sandbox.md`; that plan's Phase 0 "honest gate" is kept here as
§5.

## 1. Idea

You build and tune containers in mucli (`/containers`) and save them as **templates**. A job
should be able to say **"run in template X"**. Planning, implementation, checks and review then
all happen in an image that already has the project's toolchain. You choose the template when
you create a job by hand, and when you create jobs from Jira tickets.

## 2. What exists already (rev 3)

- `execution.environments: ["template:<name>"]` makes a job snapshot that template at start, add
  mucli's job layer, run every stage in it, and delete it at the end (`service.py:96`,
  `environments.parse_spec`).
- A template's `egress_allow` is inherited by the job's implement and checks stages, with model
  hosts dropped (`environments.py:868`).
- Jira import merges `defaults.execution` into every created job (`tracker_sync.py:188`).

The gaps are product gaps:
1. The New job sheet only offers an advanced binding table, where you type `template:x`.
2. The Jira import dialog has no environment choice.
3. A template has toolchains but not the repository's **installed dependencies**. The job's
   fresh worktree has no `node_modules` or `.venv`.
4. When checks can't run, the job reports the failures as "pre-existing".

## 3. Choosing a template

### 3.1 The "Run in" picker (one component, every surface)

```
Run in   [ ◇ rsq-node-build            ▾ ]   ☐ Remember for this workspace
          ─────────────────────────────────
          ○ Default runtime        Python 3.12 + Node 20 · no project tools
          Templates
          ◇ rsq-node-build         node 20, pnpm · setup: npm ci ×3 · 2.1 GB
          ◇ gpu-enabled-ubuntu24   CUDA · GPU · 15.8 GB   ◆ can't reach registry.npmjs.org
          Environments (snapshotted at start)
          ⬢ rust-build             running · rustup 1.83
          ─────────────────────────────────
          Advanced…                (several environments, paths, Docker, GPUs)
```

- **Options:**
  - Default runtime
  - every template (name, description, image size, GPU, setup summary)
  - every environment
  - **Advanced…**, which opens the existing multi-environment editor (monorepo paths, Docker,
    GPUs) unchanged
- **Fit warnings** come from `detect.py` over the chosen workspace. If the workspace's lockfiles
  need registries the template can't reach, the row shows `◆ can't reach registry.npmjs.org`
  with an **Allow** action, which adds the domain to the template's egress.
- **Preselection order:**
  1. the repository's remembered template (Settings → Toolchains row `repository → template:x`)
  2. the session's environment (rev 3 §4)
  3. the last template used for this workspace on this device
  4. Default runtime
- **"Remember for this workspace"** writes or updates the Toolchains row, so repository-level
  defaults also apply to scheduled Jira imports.
- **Payload:** `execution.template: "<name>"`. The server normalises it to
  `execution.environments: ["template:<name>"]` and rejects unknown templates with a readable
  error. **Advanced…** still sends `execution.environments` directly.

### 3.2 Where the picker appears

| Surface | Placement |
|---|---|
| Web New job sheet | Replaces the "Build environments" summary row. **Change** becomes **Advanced…**. |
| Web Jira import (`chooseImportTarget`) | A "Run in" field below Workspace. It applies to every ticket in the import and is sent as `defaults.execution.template`. |
| Web Jira settings (scheduled sync entries) | Each board or JQL entry gets a "Run in" select, stored on the entry as `template`. |
| Web `/containers` template card | A "Used for jobs" section: job setup commands (§4), network allowlist, "Used by N jobs". |
| Android | The same picker in the New job sheet and the Jira import sheet. |
| Terminal | `/job create --template NAME`, `/job jira import … --template NAME`, and a "Run in" step in the jobs TUI new-job prompts. |

## 4. Template job setup (dependencies)

A template can carry **job setup commands**: an ordered list of shell commands run once per job,
after the job image is built and before planning. For example:

```
npm --prefix resq-management ci
npm --prefix resq-frontend ci
cargo fetch --manifest-path stream-processor-rs/Cargo.toml
```

How they run:
- **Image:** a container from the job's template image.
- **Worktree:** mounted read-write at `/workspace`, as for implement. `/cache` is mounted for
  package caches.
- **Network:** the template's egress allowlist, with model hosts dropped.
- **Isolation:** otherwise the stage isolation: read-only rootfs, host uid:gid, `--cap-drop ALL`,
  no credentials, no model access. The commands are authored by the template owner in mucli, so
  they are trusted like Toolchains rows. They are not read from the repository.
- **Results:** stdout and stderr stream as `environment_setup` events and show in the job's
  Activity and Session views.
- **Failure:** *(changed 2026-10-15: jobs run unattended)* the job no longer parks. The failed
  `environment_setup` events and an `environment_setup_failed` event are recorded, the job carries
  a `setup_failed` warning in its metadata (shown on every surface), and it continues to planning.
  Checks that need the missing dependencies report unverified. **Continue without setup** remains
  only for jobs that parked before this change.
- **Re-runs** on `refreshEnvironments` and on retry after idle cleanup.
- **Worktree hygiene:** setup output is expected to be git-ignored (`node_modules`, `.venv`,
  `target`). If `git status --porcelain` shows new untracked or modified files after setup, the
  job warns and lists them. The checks' dirty-worktree rule ignores files produced by setup.

Templates gain two fields: `job_setup: list[str]` (default empty) and an editable
`egress_allow`. Both can be edited on the template card and through
`PATCH /api/containers/templates/{name}`. They affect only jobs started after the edit; running
jobs keep the snapshot they took.

## 5. Honest results when checks can't run

Kept from `job_build_sandbox.md` §3. A check is **`unrunnable`** when it:
- exits 126 or 127;
- prints a missing-tool signature (`sh: N: X: not found`, `command not found`,
  `Cannot find module`, `ERR_MODULE_NOT_FOUND`, an install `E403`/`ENOTFOUND`); or
- resolves a project-declared tool outside the project (for example `/usr/bin/eslint` instead of
  `node_modules/.bin/eslint`).

What changes:
- Unrunnable results are never triaged as pre-existing.
- Verification becomes **unverified**, with a message that names the commands, the cause and the
  fix. For example: "`vitest`, `tsc` and `eslint` come from `node_modules`, which isn't
  installed. Choose a template with job setup, or add `npm ci` to this template's setup."
- *(changed 2026-10-15: jobs run unattended)* The job does not park: it records `verified: false`
  and continues to review. The review is told the checks could not run and its verdict is capped
  at `needs_work`. Pushing needs no override; the PR body starts with "⚠ Checks could not run
  (unverified): <tools/causes>". **Continue unverified** remains only for jobs that parked before
  this change.
- The vocabulary gains an `unverified` status: glyph `?`, semantic `caution`.
- The runtime image drops Debian's `npm` package, which pulled in a global ESLint 6.4, so no
  stray global dev tools can stand in for project ones.

## 6. Tests

- **Payload:** `execution.template` normalisation; an unknown template is rejected; the Jira
  import with `defaults.execution.template` creates jobs bound to the template; scheduled sync
  entries carry `template`.
- **Remember:** the Toolchains row is written and drives preselection.
- **Fit warnings:** a template without `registry.npmjs.org` and a workspace with
  `package-lock.json` produce a warning, and **Allow** adds the domain.
- **Job setup:**
  - commands run in order, with the template egress, worktree read-write and no credentials;
  - a failure parks the job with the log tail;
  - setup output is excluded from dirty-worktree detection;
  - setup re-runs on refresh.
- **Honest gate:** the rsq-cloud outputs (`vitest: not found`, `tsc: not found`, ESLint 6.4)
  become `unrunnable`, the job is unverified, and the review is capped.
- **UI:** picker contract tests (web), jest (Android), and CLI flag parsing (terminal).

## 7. Implementation status (2026-10-15, branch `jobs/templates`)

Implemented for web, terminal and the backend; Android builds against the same API in parallel.

| Spec | Where |
|---|---|
| §3.1 payload, remember, fit | `execution.template` → `environments: ["template:<name>"]`, `Unknown template '<name>'`, `remember_template` writes the Toolchains row (`environments.normalize_template_choice`, `remember_template`, `remembered_template`, `template_fit`; `JobService.create_from_payload`). `GET /api/jobs/environments` templates carry `size`, `size_bytes`, `gpus`, `job_setup`, `egress_allow` and, with a workspace, `fit`; top level `remembered`. |
| §3.1/§3.2 picker | Web `static/js/work/run_in_picker.js` in the New job sheet and the Jira import dialog; a "Run in" select per Jira watcher query (`trackers.watch.queries[].template`, applied by `tracker_sync.poll_watches`). Terminal `/job create --template NAME [--remember]`, `/job jira pull … --template NAME`, and a "Run in" step in the jobs dashboard. |
| §3.2 template card | "Used for jobs" on `/containers` (job setup commands, network allowlist, "Used by N jobs"); `PATCH /api/containers/templates/{name}`; terminal `/container template setup NAME [list|add|remove|clear]`. |
| §4 job setup | `mu/jobs/sandbox/job_setup.py`, run from `ensure_environments` once per job image (so again after `refreshEnvironments` and after re-creation following idle cleanup). Checks-stage isolation (worktree rw, `/cache`, egress bridge with the template's allowlist minus model hosts, read-only rootfs, host uid:gid, `--cap-drop ALL`, no credentials, no relay). Events `environment_setup` (`{binding, command, index, status, exit_code?, output_tail?}`), output chunks as `environment_setup_log`. Failure parks with `environment_failure`; actions Retry (resume), **Continue without setup** (`skipSetup`). Files setup leaves in `git status` are listed (`environment_setup_dirty`) and ignored by the checks' dirty rule and the push gate. |
| §5 honest gate | `checks.classify_unrunnable` (exit 126/127, missing-tool signatures, registry `E403`/`ENOTFOUND`, project-declared tools missing from `node_modules/.bin`); results `status` passed/failed/unrunnable and `unrunnable {tool, cause, hint}`; summary `unrunnable`, `verified`, `unrunnable_suites`; run status `unverified`. The job parks (`verification_required`) with the cause and fix; **Continue unverified** (`continueUnverified`) reviews with the verdict capped at `needs_work`; push needs `override_reason`, and publish only proceeds with the override recorded for that check run. Vocabulary `unverified` (`?`, caution) and check status `unrunnable`. The runtime image installs Node from the pinned upstream tarball, not Debian's `npm`. |

Notes and limits:
- A stray global tool is detected for *failing* checks only (a passing check is never marked unrunnable).
- "Remember for this workspace" replaces that repository's Toolchains rows with one whole-repository row.
- Setup files that git does not ignore can still be included by an implement checkpoint; the job warns and lists them.

## 7. Environment repair (2026-10-16, user request)

> "If we do start failing checks because the environment is missing dependencies etc., we can
> allow the agent to fix the environment."

**Trigger.** A checks run where at least one suite is `unrunnable`. That covers a missing
top-level executable (`missing_tools`), a missing nested tool (`vitest: not found`), a missing
module, or a failed install. It replaces the remaining "missing tools" park: jobs never stop
for this.

**Repair step.** At most `jobs.repair.max_attempts` (default 2) per job. Each attempt:
1. Start a **repair container** from the binding's current job image:
   - the worktree read-write at `/workspace`, plus `/cache`;
   - root inside the container with `--cap-drop ALL`, then `CHOWN`, `DAC_OVERRIDE`, `FOWNER`,
     `SETUID`, `SETGID`, `FSETID` added back, plus `no-new-privileges`;
   - a writable rootfs, so package managers work;
   - no git or registry credentials; the model only through the relay.
2. **Egress** is the binding's allowlist plus, when `jobs.repair.registries = "auto"` (the
   default), the package registries `detect.py` finds for the repository, the OS package
   mirrors and toolchain installers. This applies to the repair container only; implement and
   checks keep their allowlist.
3. The job's implement harness (mucli, Claude Code or Codex) runs unattended with a repair
   prompt:
   - **Goal:** make these check commands *runnable*, not passing. The prompt includes the
     unrunnable list with output tails, the commands, `detect.py` findings, and CI files as
     hints.
   - **Rules:** don't modify tracked files; prefer project-local installs (`npm ci`,
     `uv sync`, `cargo fetch`); system packages (apt) are allowed; finish by running the check
     commands.
   - **Output:** the list of commands that fixed it.
   - **Budget:** `jobs.repair.max_cost_usd` (3), `max_runtime_s` (1200), `max_iterations` (40).
4. **Afterwards, the host:**
   - reverts any tracked-file changes (and reports them);
   - chowns `/workspace` and `/cache` back to the host uid:gid;
   - `docker commit`s the container as `mucli/job-<job12>-<binding>-repair<n>` (job-labelled,
     cleaned with the job);
   - points the binding's job image at it for every later stage and check;
   - re-runs the checks.
5. If checks are still unrunnable after the last attempt, the job continues **unverified**
   (§5), as before.

**Record and reuse.**
- `metadata.environment_repairs[]` holds attempt, commands, image, cost, outcome and transcript.
  `environment_repair` events stream to the job page.
- **Non-blocking actions:**
  - **Add to template setup** (project-level commands only, e.g. `npm ci`)
  - **Save repaired image as template**, which creates a container template from the repaired
    image with the binding's egress and setup
- Future jobs then start ready instead of repairing again.

**Settings:** `jobs.repair.enabled` (true), `jobs.repair.max_attempts`, `jobs.repair.registries`
(`auto` | `template`), and the budgets.

### 7.1 Implementation notes (2026-10-16, branch `jobs/env-repair`)

| Spec | Where |
|---|---|
| Trigger | `mu/jobs/sandbox/repair.py` `trigger_suites` / `plan_repair`: every unrunnable suite (missing top-level executables included), or a run whose new failures are all environment symptoms. Called from `verify_worker._verify` through `repair_and_recheck` before `apply_checks_result`. The former parks are gone: the environment-symptom park (`fix_attempt_skipped` + `verification_required`) now continues unverified like unrunnable checks. |
| Target | The binding that owns most trigger suites (`ready` with a `job_image`); a job without bindings repairs the shared runtime (`binding` `runtime`, image `metadata.runtime_repair_image`, applied by `policy.with_build_environment` and dropped by `cleanup_job`). |
| Container | `policy.container_args(stage="repair")`: `--user 0:0`, `--cap-drop ALL` + `REPAIR_CAPS`, `no-new-privileges`, no `--read-only`, tmpfs `/tmp`, network none + egress bridge, worktree rw, `/cache`; Docker socket and GPUs refused; role `job-repair`; `IS_SANDBOX=1` (Claude Code as root); entrypoint `REPAIR_WRAPPER` runs the worker, then `chown -R $MUCLI_REPAIR_CHOWN` on `/workspace /cache /output /harness/state` (a helper container does it when the repair was stopped first). Stage name `repair` is in `policy.STAGES` and `unattended.WRITE_STAGES` (auto-approve). |
| Egress | `repair_egress`: binding allowlist (the checks policy: Settings extra ∪ approved repository egress ∪ inherited environment egress) ∪ `detect.py` registries (`auto`) ∪ `OS_MIRRORS` ∪ `TOOLCHAIN_INSTALLERS`, then `filter_model_hosts`. Claude Code / Codex add their own provider hosts afterwards, exactly as in implement (their model is not relayed). |
| Agent and budget | `harness.stage_agent(job, "implement")`; mucli via `ModelRelay`, Claude Code / Codex via `HarnessRelay`, relay stage `repair` (cost ledger stage `repair`, synced into `job.cost_usd`). Per attempt: `max_cost_usd` (above earlier repair spend; the job cost cap still applies), `max_runtime_s` (container deadline, capped by the job runtime left), `max_iterations` (request). |
| Host steps | `_snapshot` + `revert_tracked_changes` (only paths the repair changed; implement-modified files are restored to their pre-repair bytes); `_commit`: `docker commit` with the base image's env restored for every `-e` key (no relay token baked in), user/entrypoint/cmd restored, labels `io.mucli.job/binding/home`, `io.mucli.role=job-image`, `io.mucli.repair`; secret scan (a hit discards the image); binding `job_image` / `repaired_from` / `repair_attempt` updated and the template-setup record moved to the new image so setup is not re-run; checks re-run with trigger `repair`. |
| Records | `metadata.environment_repairs[]`, `environment_repair` events (`started` / `command` / `reverted` / `finished`), `logs/repair-<n>-transcript.json` and `logs/repair-transcript.json` (session view source **Repair**). Untracked files a repair leaves are ignored by the dirty-worktree rule like setup files. |
| Actions | `addRepairToTemplate` / `saveRepairedTemplate` in `core/commands.py`, availability in `core/actions.py` (hidden from action bars, `JobFacts.environment_repair`). The saved template gets its own image (`FROM` the repaired image with `io.mucli.job=""`), so job cleanup never removes it. |
| UI | Web: `MuJobEnvironments.repairCard` in the Checks tab and the environments panel (Wash card, vocabulary `repair_outcome`), transcript link, the two actions. Terminal: `content.repair_lines` in the checks view, repair events in the session. Android: `RepairSummary` in the Checks tab (read-only), `repairOutcome` in `theme/status.ts`. |
| Settings | Section `repair` in `RULES_SCHEMA`: `jobs.repair.enabled`, `max_attempts`, `registries`, `max_cost_usd`, `max_runtime_s`, `max_iterations`. |

Known limits:
- Re-creating a job's images (`refreshEnvironments`, idle cleanup) drops the repaired image; the next unrunnable checks repair again if attempts remain.
- "Project-level" commands are picked by a heuristic (known package-manager commands without `-g`/`--global`/`sudo`/system paths).
- With Docker's classic builder (not BuildKit), the template saved from a repaired image is a child image and can keep the repaired image from being removed until the template is deleted.
