# Durable engineering jobs

A job takes a task (free text or a Jira ticket) and a workspace, then plans,
implements, checks and reviews the change in disposable containers. A person
approves the plan and decides when to push. New to jobs? Start with the
[quickstart](jobs_quickstart.md). The original milestone notes and the
first-generation lifecycle are in
[archive/durable_jobs_history.md](archive/durable_jobs_history.md).

Jobs are core runtime state, not GUI state. The terminal (`/job`, `/jobs`), the
web Jobs page and the Android app read the same view models and send the same
commands. No surface keeps its own lifecycle.

## Pipeline

```
PROVISIONING → PLANNING → PLAN_REVIEW → IMPLEMENTING → CHECKING → REVIEWING
       → REVIEW_DONE (human decision) → PUSHING → PUSHED → DONE
```

| State | What happens | Who acts |
|---|---|---|
| `PROVISIONING` | Worktree on a `mu/job-*` branch, build environment snapshots | controller |
| `PLANNING` | Read-only planner writes a versioned `plan.md` | agent |
| `PLAN_REVIEW` | Plan waits for approval. You can edit it, approve it or ask for a new plan | **you** |
| `IMPLEMENTING` | Agent edits the worktree inside the sandbox | agent |
| `CHECKING` | Host runs the approved checks without an agent and repairs new failures | controller |
| `REVIEWING` | Read-only reviewer writes `review.json` (findings + plan coverage) | agent |
| `REVIEW_DONE` | Report is ready. You can push, re-prompt with findings, recheck or discard | **you** |
| `PUSHING` | Squash, secret scan, push the branch and open a draft PR | controller |
| `PUSHED` | The PR is open. Merges are detected automatically; a closed PR needs you | controller / **you** |
| `DONE` | Finished; build environment images are released | — |

`NEEDS_INPUT` pauses any active stage on a question, a tool approval, a reached
limit or a stall. `FAILED` keeps the stage that failed so `retry` can restart it, and
`CANCELLED` is terminal. No limit ever fails, cancels or times out a job (see
[Limits pause and ask](#limits-pause-and-ask)); the old `timed_out` and
`budget_exceeded` statuses are no longer written and stay readable (and
retryable) on old rows. The stored `status` column still holds legacy values
for compatibility (`publishing` = `PUSHING`, `ready_for_review` = `PUSHED`,
`merged` = `DONE`). The job's state comes from the core state model in
`mu/jobs/core/states.py`.

### Workspace

Every job runs in a workspace (`workspace_root`). By default this is the first
folder of the session that creates it. In the terminal, `--in PATH` picks the
workspace directly, and no chat session is needed. The job is filed under a
hidden per-workspace session that is kept out of chat session lists. A
payload may name `repository` instead (see below). Non-git workspaces are
allowed; they run with `execution.git_enabled = false`, without a base commit,
review branch or PR. Jobs stored before v2 that have `repository` set still load.

### Jobs on any repository

`repository` (alias `repo`) is an alternative to `workspace_root`:

- A **local path** is used as the workspace.
- A **URL** is cloned: `https://host/org/repo(.git)`, `ssh://git@host/org/repo`,
  `git@host:org/repo.git` or `file:///path/repo.git`, optionally suffixed with
  `@branch` or `#branch`. The clone lives in
  `$MUCLI_HOME/repos/<host>/<org>/<name>-<sha8(url)>` (`--filter=blob:none`) and
  is refreshed with `git fetch --prune` on later jobs. A directory at that path
  that is not a clone of the same URL is never touched. Authentication uses the
  host's git credential helpers and SSH agent, never a prompt
  (`GIT_TERMINAL_PROMPT=0`, ssh `BatchMode`). URLs with an embedded password or
  token are refused so credentials never reach job records. Submodules are
  initialised (`jobs.clone.submodules`, default on). With `jobs.clone.lfs = auto`,
  LFS files are pulled when `git-lfs` is installed; otherwise the job gets a
  `repository_warning` event. Clone and fetch time out after ten times
  `jobs.git_timeout_seconds`.

A cloned job starts from the fetched tip of the branch (`base_branch`/`base_sha`
default to the `@branch` or the remote default) and records
`metadata.repository_url` (credential-free), `metadata.repository_clone` and,
when relevant, `metadata.repository_warnings`.

API: `POST /api/jobs` with `{"title": ..., "repository": "<URL|path>", ...}`.
`workspace_root` may be omitted. If both are given they must name the same
location. Session defaults (model, mode) are looked up for the clone path. A
clone failure returns `400` with
`{"detail": "<actionable message>", "error": {"kind": K, "host": H}}`, where `K`
is one of `invalid_url`, `credentials_in_url`, `auth_failed`, `not_found`,
`timeout`, `path_conflict`, `branch_not_found` or `clone_failed`. Other
validation errors keep the usual `400 {"detail": "..."}`. In the terminal,
`/job <KEY|title> --in <URL|PATH>` and `/job create <title> --repo <URL|PATH>`
do the same. Each clone gets its own hidden session.

Job worktrees initialise submodules after `git worktree add`. When the network
is unavailable this fails without stopping the job and is recorded as a
`worktree_submodules_skipped` event.

### Plan

The planner writes `plan.md` (versions in `plans/plan.v<N>.md`) with acceptance criteria, likely files
and the checks it proposes. Approval records an exact version. Approving a stale
version returns a conflict, and `/job approve <id>` without a version approves
the latest one. Plan approval is on by default (`plan_approval` in job
settings). Drafts autosave on web and Android. Saving a draft creates a new
numbered version.

### Acceptance criteria and checks

The planner's criteria and validation commands are merged into the job contract
(`mu/jobs/dynamic_checks.py`):

- **Validation commands are proposed by the planner.** Creating a job does not
  require any. User-supplied criteria and commands (`--accept`, `--check`) are
  never dropped and always come first.
- Planner commands must start with an allow-listed executable (pytest, npm,
  make, cargo, go, tsc, …). Shell operators (`; && || | > < ` $( &`) are
  rejected. Rejected commands are recorded in `plans/contract.json`.
  `execution.validation_allowlist` extends the allow-list for one job. At most
  8 planner commands are accepted.
- If no commands remain, you can add checks in the plan editor before you
  approve. When plan approval is off, a job with no checks parks with
  `no_validation`.

Configured suites also run: `[[checks.suites]]` in the repository's
`.mucli/jobs.toml` (`name`, `command`, optional workspace-relative `junit`), or
`checks.suites` in job settings. Commands are validated again at execution and
run without a shell. Pytest suites produce JUnit evidence automatically.

A failed suite runs again on the job's base commit in a disposable worktree.
Failures that also happen there are reported as preexisting. Only new failures
start the repair loop, which is capped by `checks.max_fix_attempts` (default 2).
Summaries, JUnit and logs live under `checks/run-<n>/`.

### Review (report-only)

The reviewer writes `review.json` with anchored findings and plan coverage. The
host computes merge readiness from open findings and the actual check evidence.
**Every completed review stops at `REVIEW_DONE`**, and nothing is sent back
automatically. You decide what happens next:

- update finding dispositions (`mark-findings`);
- re-prompt the implementer with selected findings and guidance
  (`/job reprompt <id> F-1,F-2 guidance`);
- recheck after manual edits (`recheck`);
- push (`push`), or discard (`discard`).

A blocking review (`do_not_merge`) needs an explicit, audited override reason to
push. Passing checks are required either way. The pipeline never merges.

### Push and PR

Push requires a passing check run for the current commit and a clean worktree.
A review override cannot bypass this gate. When you push, the job enters
`PUSHING`. That stage squashes the job's checkpoint commits onto the PR base as
one commit. The commit carries the ticket key and a `Mucli-Job: <id>` trailer
and honours `commit.gpgsign`. The stage checks that the commit's tree is the
verified tree and secret-scans the diff. It then pushes the branch and opens a
**draft** PR (or updates the job's existing PR on a re-push):

| `publish.mode` | Behaviour |
|---|---|
| `auto` (git default) | `gh` if installed and origin is GitHub; otherwise REST chosen by origin host |
| `gh` | `gh pr create --draft` |
| `github` | GitHub REST (`GITHUB_TOKEN` / `GH_TOKEN`) |
| `gitlab` | GitLab merge request titled `Draft: …` (`GITLAB_TOKEN`) |
| `bitbucket` | Bitbucket Cloud pull request (`BITBUCKET_TOKEN`) |
| `gitea` | Gitea/Forgejo pull request (`GITEA_TOKEN`) |
| `none` (non-git default) | Stage skipped |

On success the PR details go into `metadata.pr` and the job moves to
**`PUSHED`**. Every `publish.merge_poll_seconds` (default 300 s; `0` = off) the
controller asks the PR host for the PR's state. A merged PR moves the job to
`DONE`. A PR closed without merging puts the job in front of a human: **Re-push**
opens the PR again from the job branch, and **Mark done** closes the job out
without a merge. `/job mark-merged <id>` (the **Mark merged** action) is the
manual override when polling is off or the host cannot be queried. The web and
Android job header shows the PR number and state as a link. If the
push or PR call fails, the job pauses for you with `publish_failed`. Tokens come
from the environment only and are redacted from error text.

### Models, agents and limits

Each stage can use its own model. The lookup order is stage, then job
execution, then Settings → stage models, then the creating session:

```json
{"execution": {
  "stages": {
    "plan":      {"provider": "anthropic", "model": "opus"},
    "implement": {"model": "haiku"},
    "review":    {"model": "opus"}
  }
}}
```

Each stage can also run on mucli, Claude Code or Codex
(`--agent plan=claude,implement=codex`; see `/job agents`). The default job cost
cap is `defaults.max_cost_usd` = $100. A job created without a cost uses it; an
explicit `max_cost_usd: 0` (enter `0` in the New job sheet) means no cap. Stage budgets,
timeouts and iteration limits are listed in
[configuration.md](configuration.md#job-settings).

### Limits pause and ask

Every cost, token, time and iteration limit works the same way: reaching it
**pauses** the running stage and asks you whether to continue. The job is never
failed, cancelled or timed out by a limit.

| Limit | Where it is set | Scope |
|---|---|---|
| Job cost | `max_cost_usd` on the job (New job sheet; default `defaults.max_cost_usd`) | whole job |
| Job runtime | `max_runtime_seconds` on the job (default `defaults.max_runtime_seconds`) | whole job, worker-active time |
| Stage cost / token budget | `sandbox.max_cost_usd`, `sandbox.max_tokens` | one stage, across all its runs in the job |
| Stage time limit | `sandbox.stage_timeout_s.<stage>` | one run of a stage |
| Iterations | job `max_iterations`, `limits.stage_max_iterations.*`, `limits.max_iterations` | one run of a stage |

When a stage reaches one, the stage stops the same graceful way as before a
resume (its conversation, transcript and Claude Code / Codex session are kept),
its cost is recorded, and the job moves to `NEEDS_INPUT` (`needs_human`) with
attention `limit_reached`. `metadata.limit_reached`, the `limit_reached` event
and the pause's `status_changed` payload (`limit`) carry
`{kind: cost|tokens|time|iterations, scope: job|stage, stage, used, limit, unit}`
plus a message such as "Job reached its $100.00 cost limit during implement
($100.03 used). Approve more to continue, or cancel." Ticket writeback and the
Android "needs you" notification use that message.

You then choose:

- **Approve more & continue** — the `raiseLimit` command,
  `{kind, scope, stage, amount}`. The amount is prefilled (the limit again; half
  of it for time) and accepts `25`, `25usd`, `30m`, `2h` (a bare number is
  minutes), `5M`, `500k`. Job limits raise the job's own `max_cost_usd` /
  `max_runtime_seconds`; stage limits add a per-job allowance in
  `metadata.limit_grants` (global Settings never change). A `limit_raised` event
  records the old and new limit. The paused stage then continues its own
  conversation.
- **Cancel job** — the existing discard.

`raiseLimit` also works ahead of time on a queued or running job (terminal
`/job raise-limit <id> <cost|tokens|time|iterations> <amount> [--stage S]
[--scope job|stage]`; cost and time default to the job limits, tokens and
iterations to the current stage).

The job runtime limit pauses gracefully: each agent stage's container time
limit is capped at the job's remaining runtime, so the stage pauses right at
the limit, and a stage that would start after the limit pauses before it
starts. The controller never kills a worker for it; an agent stage still
running two minutes past the limit is asked once to pause
(`stage_pause_requested`). Checks and push finish on their own. A job with no
cost cap never pauses for that cap. Per-request model and tool-command timeouts
are not limits on the job and still apply as before.

Implementation writes need your approval unless the job runs unattended. That
is the explicit per-job `--auto-approve` flag, or the Settings default
`defaults.run_unattended` (off). A pending write shows separate Approve tool /
Deny tool actions (`/job approve-tool <id>`, `/job deny-tool <id> [reason]`).
Each approval is consumed once, for the exact tool name and arguments.

Repo-declared images, environments and network access need approval too. A
repository can ask for its own build image, devcontainer or Dockerfile and for
extra egress. Nothing it declares is used until a human approves it, per
repository and fingerprint. A job that hits an unapproved request pauses with
**Approve environment** / **Reject environment** actions
(`/job approve-environment <id>`, `/job reject-environment <id> [reason]`).
The New job sheet offers the same approval up front, through
`POST /api/jobs/environments/approve` with
`{"repository": PATH, "fingerprint": "...", "session": ""}`. Only a fingerprint
the repository currently requests is accepted (409 otherwise).

## Jira intake

Link Jira from **Settings → Jira** on the web (or the JIRA step of
`/job setup`). The base URL, email and API token (Cloud) or PAT (Data Center)
are stored in `$MUCLI_HOME/integrations/jira.json` (mode 0600). Environment
variables override the file field by field:

| Variable | Purpose |
|---|---|
| `JIRA_BASE_URL` | `https://acme.atlassian.net` or Data Center URL |
| `JIRA_EMAIL` + `JIRA_API_TOKEN` | Cloud (basic auth, REST v3) |
| `JIRA_PAT` | Data Center personal access token (bearer, REST v2) |
| `JIRA_WRITEBACK` | `1` to post a "job created" comment (off by default) |

Credentials are never taken from API payloads and never stored on a job.

- Terminal: `/job PROJ-1 [--in PATH|URL]`, `/job jira pull PROJ-1 PROJ-2`, or
  `/job jira pull --jql "project = PROJ AND assignee = currentUser()"`.
  Add `--force` to start a new job for a ticket whose latest job is done.
- Web and Android: the Tickets page lists boards and JQL results. Select
  tickets, choose **Create jobs**, then pick the workspace or paste a git URL
  (the session is optional; a saved session on that workspace supplies model,
  mode and environment). **Run again** re-imports tickets whose latest job is
  done. A ticket key typed in the New job sheet imports that ticket into the
  sheet's workspace or repository.
- API: `POST /api/jobs/jira/import` with `{"keys": [...]}` or `{"jql": "..."}`,
  plus optional `defaults` (`workspace_root`, or `repository` = git URL or path,
  which is cloned like `POST /api/jobs`), `session_name` and `force`. Bodies
  that contain credential fields get a 400. Clone failures return 400 with
  `{"detail", "error": {"kind", "host"}}`. A body with no workspace, repository
  or session workspace gets a 400. Jira errors return 502.

Each issue becomes one job. The title is `KEY: summary`. The description is the
issue text with ADF converted to plain text, and acceptance criteria come from
an "Acceptance criteria" heading. `metadata.jira` holds the key, URL, status
and type. An issue that already has a live job is skipped, and so is one whose
latest job is done unless `force` is set. Ticket text is wrapped in
UNTRUSTED TICKET CONTENT markers for the agent. Receipts, History search and
exports strip the markers.

### GitHub issues

`POST /api/jobs/tracker/import` with `{"tracker": "github", "keys":
["owner/repo#7", "https://github.com/owner/repo/issues/8"]}` (or `"query"`, a
GitHub issue search) imports GitHub issues the same way, with the same
`defaults`, `session_name`, `force` and error handling as the Jira import.
`{"tracker": "jira"}` is the Jira import. GitHub access uses `GITHUB_TOKEN` /
`GH_TOKEN`, or the logged-in `gh` CLI when no token is set. Credential fields in
the body get a 400.

### Ticket watcher

The watcher imports new tickets from saved queries. It is off by default.
Configure it under `trackers.watch` in `$MUCLI_HOME/integrations/jira.json`:

```json
{"trackers": {"watch": {"enabled": true, "interval_seconds": 900, "queries": [
  {"tracker": "jira", "query": "labels = mucli", "workspace_root": "/src/app"},
  {"tracker": "github", "query": "repo:acme/api label:mucli", "workspace_root": "/src/api"}
]}}}
```

The job controller runs each query at most once per `interval_seconds`
(default 900, minimum 60). Each query needs a `workspace_root`; `session_name`
and `max_results` (default 10, at most 50) are optional. Imports use the normal
duplicate protection. `POST /api/jobs/tracker/watch/poll` runs the queries that
are due now.

## Sandbox

Plan, implementation and review run in disposable containers from
`mucli-job-runtime:latest`. Build it with `/job setup runtime build` or
`python3 -m mu.jobs.sandbox.build`. The build context holds only runtime source
and the public pricing registry. Host sessions, credentials, `.git` and
reference archives are left out. With no Docker or no image, jobs fail with an
actionable error. There is no host agent fallback.

The container runs as the host UID/GID with dropped capabilities, a read-only
root filesystem and `--network none`. A stage-scoped, authenticated model relay
is exposed through a Unix socket, so provider credentials stay on the host. Plan
and review mount the workspace read-only. Implementation may edit the worktree,
and the host validates paths and creates the commits. Plan mode and tool
approvals remain enforced.

Git metadata is visible but read-only, so VCS-aware tools work (`go build`
with `GOFLAGS=-buildvcs=auto`, setuptools-scm, `git status`/`log`/`describe`).
The worktree's private gitdir is mounted at `/mucli-git/common/worktrees/job`,
and the common dir's `objects`, `refs`, `packed-refs`, `info` and `shallow` sit
beside it. The container gets a rewritten `/workspace/.git` pointer, a generated
`commondir` and a sanitized `config` that holds only core/format keys and
`core.hooksPath=/dev/null`, so no remotes, tokens or credential helpers. Hooks
are never mounted. `GIT_OPTIONAL_LOCKS=0` is set.

Plan, implementation and review each default to 1,000 agent iterations per
run. `max_iterations` on a job applies to all three.
`limits.stage_max_iterations.*` overrides each stage. Reaching it pauses the
stage and asks you for more iterations.

Package registry access goes through the domain/IP-checking egress proxy on a
second Unix socket (`sandbox.egress_extra`, implementation and checks only).
`sandbox.cpu`, `memory`, `stage_timeout_s.*`, `max_tokens` and `max_cost_usd`
bound each stage; reaching a budget or time limit pauses the job and asks you
(see [Limits pause and ask](#limits-pause-and-ask)). Usage is recorded in the
job's `costs.jsonl` (with the tokens charged to the stage budget). A request
that was already sent can still be charged when its stream is cut off at a
budget limit.

## Build environments

A project's toolchain comes from a *build environment*: a container-mode
environment, a container-mode template or any local image. The design is in
`documentation/plans/job_build_environments.md`.

**Choosing the set.** The set is resolved once, before the first sandboxed
stage, from the first source that yields bindings. Sources are never merged.

1. the job override: `execution.environments` from the New job sheet,
   `/job create --env name[:paths][:docker]` (repeatable) or `setEnvironments`;
2. the repository's `.mucli/jobs.toml`:
   ```toml
   [[environments]]
   name = "rust-build"          # an environment, template:<name> or image:<ref>
   paths = ["resq-backend", "stream-processor-rs"]
   primary = true               # optional: agent stages run here

   [[environments]]
   name = "compose-it"
   paths = ["."]
   docker = true                # private per-job Docker engine (needs Sysbox)
   gpus = "all"                 # "all" | "0,1" | "" ; default: the environment's own request
   preload_images = ["postgres:16"]
   ```
3. the job's container session (`environments.use_session_environment`);
4. Settings → Environments → Toolchains rows (`sandbox.toolchains`:
   repository → base image, `template:<name>` or `environment:<name>`, plus
   optional paths, Docker and GPUs);
5. otherwise the shared `mucli-job-runtime` image.

**Per-job snapshots.** Each binding is snapshotted for that job alone with
`docker commit --pause=true` and the secret-env scrub. `/root` is filtered:
secrets, mucli state, shell history and caches are removed, and a `/root`
volume is captured as a filtered tar. The login shell's PATH and toolchain
homes are probed and baked in, and mucli's job layer is added. A secret scan
of `/root`, `/home` and `/etc` follows. A scan hit parks the job and names the
paths (allow paths with `environments.secret_scan_allow`). Images carry
`io.mucli.job=<id>` and are removed when the job is done, cancelled, pushed,
archived or deleted. Failed and waiting jobs keep them for
`jobs.environment_retention_h` (72 h) unless the job is kept
(`keepEnvironments`). `refreshEnvironments` takes the snapshots again.

**Several environments.** Agent stages run in the primary binding: the one
marked primary, or else the one that owns most of the plan's likely files. Each
check suite runs in the binding that owns it. That is its explicit `env`, else
its `cwd`, else a path its command names, else the primary. With several
bindings the implementer gets `run_in_environment(env, command, cwd)`, run in a
sibling container with the same isolation (approval rules as `bash`).

**Egress and GPUs.** Implement and checks may reach `sandbox.egress_extra` plus
the environment's egress allowlist (`environments.inherit_egress`).
Model-provider hosts are always dropped. GPU bindings get `--gpus`
(`sandbox.gpus_enabled` turns this off for all jobs). A host without a usable
NVIDIA runtime parks the job.

**Docker-in-Docker.** `docker = true` gives the job a private engine
(`sandbox.docker_engine_image`, default `docker:dind`), under the Sysbox runtime
only. There is no privileged fallback. The engine sits on an internal network
and mounts the worktree at `/workspace`. Stages reach it only through a
per-stage policy proxy at `/var/run/docker.sock`. The proxy labels every object
and allows bind mounts only from `/workspace` and `/cache`. It refuses host
devices, host namespaces and other jobs' containers, and enforces
`sandbox.docker_storage_gb`. It audits `docker_api` events. Pulls are allowed
only from registries on the job's egress allowlist. Services that tests start
themselves (testcontainers, compose) need DinD. Running such checks inside the
job engine is not supported yet.

## Surfaces

The terminal, web and Android show the same six-step pipeline and the same
server-provided actions. Job detail has the same tabs everywhere: **Session,
Plan, Diff, Review, Checks, Diagnostics, Cost & audit**. Setup includes Doctor as a
tab (web Jobs → Setup, Android Job settings → Setup, terminal `g u` and `g d`).

- Terminal: `/jobs` opens the keyboard dashboard. The `/job` subcommands are
  listed in `/help` and [commands.md](commands.md). Action names use
  lowercase hyphens (`approve-plan`, `save-plan-draft`, `restart-stage`,
  `mark-merged`). The camelCase ids are still accepted.
- Shared HTTP: `GET /api/jobs/vm/home`, `/vm/jobs`, `/vm/job/{id}` (optional
  `parts`); `POST /api/jobs/cmd` with `{type, job_id, args}`;
  `GET /api/jobs/events` (coalesced over 150 ms, naming the parts that changed).
  Disabled commands return 409 with their reason. Malformed commands return 400.

## Storage

Each job's folder is `$MUCLI_HOME/jobs/<id>/`. It holds the worktree,
`ticket.json`, versioned plans, `diff.patch`, `review.json`, check evidence,
stage transcripts and `costs.jsonl`. The job database is
`$MUCLI_HOME/jobs/jobs.sqlite3`, and job settings are
`$MUCLI_HOME/jobs/settings.json`. Older job paths are still supported. When an
old job is opened, its embedded plan and review data is backed up to
`legacy-handoffs.json`. Compatibility endpoints pass lifecycle commands to the
core. Raw status and attention mutation endpoints return 410.
