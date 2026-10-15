# Plan: run a job in a container template

Status: approved direction (2026-10-15) · Owner: jobs · Builds on: `job_build_environments.md`
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
- **Failure:** the job parks with `ENVIRONMENT_FAILURE` and the log tail. Actions: **Retry**,
  **Edit template** (opens the template card), **Continue without setup**.
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
- Verification becomes **unverified**, and the job parks with a message that names the commands,
  the cause and the fix. For example: "`vitest`, `tsc` and `eslint` come from `node_modules`,
  which isn't installed. Choose a template with job setup, or add `npm ci` to this template's
  setup."
- **Continue unverified** caps the review verdict at `needs_work`, and pushing needs an override.
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
