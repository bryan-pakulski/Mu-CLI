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

`NEEDS_INPUT` pauses a job on a reached limit, a stall or a genuine failure
that needs a decision. Stages never pause on a tool approval or a question: jobs
run unattended (see [Unattended stages](#unattended-stages)). `FAILED` keeps the stage that failed so `retry` can restart it, and
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
payload may name `repository` instead (see below). A workspace that is not a git
repository runs on a git snapshot of it (see **Folders** below). Jobs stored before v2
that have `repository` set still load.

The job's working directory (its worktree, on this machine) shows in the job's
summary with **Open a shell**: a `cd '<worktree>'` command to copy into your own
terminal for manual edits (also in the command palette). Nothing runs from the
browser. Pause a running job first, and commit your edits on the job branch before
resuming: the checks fail on uncommitted changes.

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

### Sources

The New job sheet's **Source** picks where the code comes from. The API takes the same
choice as `source` (`mu/jobs/sources.py`) instead of `workspace_root`/`repository`:

- `{"kind": "folder", "path": "/src/app"}`: any local folder, git or not. A saved
  session on that folder still supplies its defaults.
- `{"kind": "repository", "name": "backend"}`: an entry of **Settings → Repositories**
  (`jobs.repositories`: rows of name, URL or path, optional branch). URLs are cloned as
  above, with the row's branch as `#branch`; for local paths the branch becomes
  `base_branch`. `{"kind": "repository", "url": "..."}` takes a URL that isn't in the list.
- `{"kind": "session", "session": "chat-1", "include_conversation": true}`: runs in the
  session's first workspace with its model, mode and environment. `include_conversation`
  attaches the session's conversation (its summary and user/assistant text, no tool
  output) as `conversation-<session>.md`. A session without a workspace is refused.

The resolved choice is recorded as `metadata.source`. Ticket imports accept `source`
in their `defaults` too. The Android New job sheet offers the same Source switch and attachments (phone
files are sent base64-encoded; a session's files by id), and a job's attachments save to
the phone from its summary.

### Folders

A folder that is not a git repository (`source.kind = folder`, a session's workspace or
`workspace_root`) runs on a managed git snapshot of it (`mu/jobs/folder_snapshot.py`). The
folder is mirrored into `$MUCLI_HOME/repos/folders/<name>-<sha8>` (branch `main`) and
committed; every new job on that folder refreshes the snapshot first, committing only when the
folder changed, so the job starts from the folder as it is now. Left out (and listed in
`metadata.folder_snapshot.skipped`): `.git` and dependency/cache directories (`node_modules`,
virtualenvs, `__pycache__`, ...), secret files by the sandbox pre-check's rule (env templates and
test fixtures are kept), symlinks that are absolute or leave the folder, and anything the folder's
own `.gitignore` files ignore. Folders over `jobs.folder_snapshot_max_mb` (Settings → Git & PR
hosts, default 2048 MB) are refused.

The folder itself is never written. A folder job never publishes (`publish_mode = none`): its
result is the patch from `GET /api/jobs/<id>/patch` (binary-safe, the whole change), applied in
the folder with `git apply <file>`. The job summary on the web and Android links it. In review,
**Finish (keep as patch)** replaces Push / open PR, and **Mark done** closes the job once the patch
is applied.

### Multiple sources

A job may change several sources. `source` (or `workspace_root` / `repository`) is the primary; it
runs at `/workspace` exactly as above. `sources` adds up to 8 more, each in the `source` shape
(`folder`, `repository` by Settings name or `url`, `session`) plus an optional `alias` for its
mount name (`mu/jobs/multi_source.py`). Each is resolved like the primary when the job is created
(URLs cloned, plain folders snapshotted, git repositories from their current HEAD; a source that
is already part of the job is refused) and recorded in `metadata.sources`.

When the job is prepared, every extra source gets its own worktree on the job's branch name
(`<jobs>/<id>/sources/<name>/<id>`). In the sandbox it is mounted at `/sources/<name>`: writable in
implement, checks and (via the broker) build environments, read-only in plan and review, never
in repair or template job setup, with its `.git` pointer mounted read-only so the agent cannot point
the host's git elsewhere. The stage session lists it as a folder and the plan, implement and review
prompts name it. A check suite may set `cwd` to `/sources/<name>/...`.

The host guard (changed paths, secret files, credentials in the diff) and checkpoints cover every
source; `diff.patch`, the Diff tab, `GET /api/jobs/<id>/diff` and the review prompt include each
extra source's change with paths under `sources/<name>/`. Push opens one draft PR per extra source
that has a remote and changes (before the primary's; a failure parks the job with the source's
name), recorded per source (`metadata.sources[].pr`). Folders, and repositories without a remote,
hand their change back as a patch: `GET /api/jobs/<id>/patch?source=<name>`. Archiving a job removes
the extra worktrees and managed branches too. The merge poller and base-drift checks follow the
primary only. The New job sheet (web and Android) has **Other sources**; the job summary lists each
source with its PR or patch.

### Attachments

`attachments` (`mu/jobs/attachments.py`) gives files to a job at creation. Each item is
an upload (`{"name", "content_base64", "mime_type"?}`), a chat session's attachment or
artifact (`{"session", "attachment_id" | "artifact_id"}`) or its conversation
(`{"session", "conversation": true}`). The limits are 20 files, 25 MiB per file and
100 MiB in total. Every item is checked before the job is created, so a bad item returns
`400` and no job is made. Files are stored in `$MUCLI_HOME/jobs/<id>/attachments/`, with
names flattened and de-duplicated, and are listed in `metadata.attachments` (name, size,
sha256, mime type, origin). Every stage gets a copy at `/artifacts/attachments/` (read-only,
and a read-only second folder of the mucli stage session), and the plan, implement and
review prompts list them. Attachments never enter the worktree, so they are never
committed or pushed. Download them with `GET /api/jobs/<id>/attachments/<name>`; the job's
summary links them.

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

Checks that could not run first get an unattended environment repair (see
Build environments → Environment repair) and a re-run; only then is the run
recorded unverified.

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
push. Passing checks are required either way, except that checks which could
not run (unverified) need no override: the review verdict is capped at needs
work and the PR body starts with "⚠ Checks could not run (unverified):
<tools/causes>". The pipeline never merges.

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

### Unattended stages

Jobs run unattended (user decision, 2026-10-15). No stage prompts anyone or
parks on an in-stage prompt, on every agent harness (mucli, Claude Code, Codex):

- **Implement and checks** approve every tool call automatically. The sandbox is
  the control: container isolation, a read-only root filesystem outside the
  worktree, the egress allowlist and no credentials.
- **Plan and review** are read-only. A write or side-effecting tool call is
  denied automatically ("This is the read-only plan stage; describe the change
  instead."); read-only tools run without asking. Claude Code and Codex run with
  their permission prompts off and the write tools removed in these stages; a
  permission request that still reaches the worker is decided the same way.
- **Questions** (ask_user, choices, quizzes, confirmations, input) are answered
  with: "No one is available to answer during an automated job stage. Proceed
  with your best judgement, state the assumption in your summary, and continue."
  Claude Code stages do not offer `AskUserQuestion`. The planner lists the
  assumptions it made under the plan's **Risks / out of scope** section.

Each automatic decision is recorded as an `auto_permission` or `auto_answered`
event and shown in the job's session view. There is no per-job auto-approve
choice: `execution.auto_approve_writes` is accepted for compatibility and always
stored as true, `/job create --auto-approve` / `--no-auto-approve` are accepted
and ignored, and the `defaults.run_unattended` setting is gone. A job that
parked on a tool approval before this rule is resolved by the controller on its
next tick (or by **Resume**): the pending call is approved in implement/checks
or denied in plan/review, and the stage continues. **Approve tool** / **Deny
tool** (`/job approve-tool <id>`, `/job deny-tool <id> [reason]`) still work for
such a job until then.

The human gates are pipeline gates, not in-stage prompts: plan approval
(`plan_approval` → `PLAN_REVIEW`) and the push after `REVIEW_DONE`. Limits
(cost, time, tokens, iterations) still pause and ask, and genuine failures
(provider errors, a lost worker) still need you.

Repo-declared images, environments and network access need approval before
they are used. A repository can ask for its own build image, devcontainer or
Dockerfile and for extra egress. Nothing it declares is used until a human
approves it, per repository and fingerprint, but an unapproved request never
parks the job: the job falls back to the next environment source (session
environment, Toolchains, the default runtime) and records a
`repository_environment_skipped` event. The job page offers **Approve
environment** / **Reject environment** for it as a non-blocking card
(`/job approve-environment <id>`, `/job reject-environment <id> [reason]`);
the approval applies to future jobs on that repository, while the running job
keeps the environment it started with. Unapproved repository egress is likewise
left out until approved. The New job sheet offers the same approval up front, through
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
and the host validates paths and creates the commits. Plan mode stays
enforced; tool approvals are decided automatically by stage (see
[Unattended stages](#unattended-stages)).

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
of `/root`, `/home` and `/etc` follows. A scan hit means the image is never
used: the job falls back to the next environment source and records a
`build_environment_skipped` event naming the paths (allow paths with
`environments.secret_scan_allow`). Images carry
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

**Egress and GPUs.** Implement and checks may reach `sandbox.egress_extra` (by default the npm, Cargo/crates.io, Maven/Gradle and PyPI registries) plus
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

**Evidence** (`mu/jobs/evidence.py`). The implement and review prompts ask the
agent to save what it observed while testing (screenshots, short recordings,
text logs) to `/output/evidence/`, optionally with `index.json`
(`[{"file", "caption"}]`). After each stage the host copies regular files (no
symlinks or dotfiles) into `evidence/<stage>-<stamp>/` in the job's artifacts,
appends them to `evidence/manifest.json`, redacts secrets from text files and
emits `evidence_added` (Checks tab and a note refresh). The `checks` part lists
them as `evidence`; `GET /api/jobs/{id}/evidence/{file}` serves only manifest
files: images and video inline, anything else (SVG and HTML included) as a
`text/plain` or binary download with `nosniff` and a sandboxing CSP. Evidence is
never committed or pushed. The runtime image ships Playwright's headless
Chromium (`PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright`, world-readable), so an
agent can run `playwright screenshot --full-page <url> /output/evidence/x.png`;
template-derived images install it best-effort at build (a base Playwright cannot
provision still builds), and Playwright's browser hosts are in the default
package egress so `playwright install chromium` works where it is missing.

mucli stage agents (plan and review included: it is in the read-only allowlist) also have the
`browser_snapshot` tool. Inside the stage container it reaches the container's own localhost (the dev
server the agent started), emulates a desktop, tablet or phone viewport and light/dark mode, can
capture one element and run up to 20 steps (click, fill, press, hover, select, wait) on local pages,
and saves each screenshot into `/output/evidence/` with a caption, so it becomes job evidence. The
model sees the screenshot itself on its next turn (vision models).

**Check artifacts** (`mu/jobs/check_artifacts.py`). Before checks discard what a suite
left in the worktree, every file it wrote during the run (untracked *or* git-ignored:
JUnit and coverage XML, logs, HTML reports, screenshots, packet captures, archives)
is copied into `checks/run-<n>/artifacts/<suite-N|suites>/<path>` and listed in
`checks/run-<n>/artifacts.json`. Tracked files are never collected (an edit there
is a source change and stays for the dirty-tree gate). Dependency and cache
directories (`node_modules`, `__pycache__`, `.venv`, …) and symlinks are skipped; a
file is capped at 64 MB, a suite at 500 files and a run at 512 MB (what was left out
is listed). Text artifacts are secret-redacted on the way in, like evidence.
Collection never fails the checks. The Checks tab's **Artifacts** browser for the
selected run lists suite output, JUnit reports and the collected files, with a
viewer per kind: logs and text (line numbers, filter, errors-and-warnings only,
jump to the first error), JUnit (cases, failures with their message and trace),
XML and JSON (pretty-printed; XML that declares a DOCTYPE or entities is never
parsed), CSV tables, images, HTML reports (in a frame with no scripts), packet
captures (pcap/pcapng: packets with source, destination, protocol and a summary
for DNS, HTTP, TLS, TCP flags, ARP and ICMP; conversations), archives (contents)
and a hex dump for anything else. Routes: `GET /api/jobs/{id}/checks/{run}/artifacts`
(the listing), `…/preview?path=` (what the viewer shows) and `…/files/{path}` (the
file: images inline, HTML only as a sandboxed document with `?view=1`, everything
else as a download, always `nosniff` with a sandboxing CSP). Nothing outside the
run's folder is served.

**Job code in stage containers.** Every stage, check and repair container
mounts the host's job code (`mu`, `providers`, `utils`, `prompts`, `mucli.py`,
`config/model_pricing.json`) read-only over the image's `/opt/mucli` copy
(`sandbox.code_overlay`, default on; `build.code_overlay_mounts`). A template or
repaired image built by an older mucli then still runs the current worker, so
new stages and protocols work without a rebuild. Before this, such an image ran
its baked worker, which treated every stage but implement as read-only, so a
repair agent had no shell. The overlay is skipped when the tree holds a file
the secret-path policy denies. With the overlay, only dependency drift
(`io.mucli.deps`: `requirements.txt` and the Dockerfile) marks an image stale.

**Environment repair** (`documentation/plans/job_templates.md` §7,
`mu/jobs/sandbox/repair.py`). When a checks run has suites that could not run
(unrunnable: a missing top-level executable, a missing nested tool, a missing
module, a failed install), or every new failure is an environment symptom, the
verify worker runs an unattended **repair** stage before it records the run:

- the job's **implement** agent (mucli, Claude Code or Codex) with a repair
  prompt (the unrunnable commands with output tails, `detect.py` findings, CI
  files as hints; goal: make the commands *runnable*; don't modify tracked
  files; prefer project-local installs; finish with the commands that fixed it);
- in a repair container from the binding's current job image (or the shared
  runtime image): root inside with `--cap-drop ALL` plus `CHOWN`,
  `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID`, `FSETID`,
  `no-new-privileges`, a writable rootfs, tmpfs `/tmp`, the worktree read-write
  and `/cache`, no git or registry credentials, no Docker socket, no GPUs, the
  model through the relay;
- egress = the binding's allowlist ∪ (with `jobs.repair.registries = auto`) the
  detected package registries ∪ OS mirrors ∪ toolchain installers, model hosts
  dropped. Implement and checks keep their own allowlist.

The container's entrypoint chowns `/workspace` and `/cache` back to the host
uid:gid when the agent exits. The host then reverts tracked files the repair
changed (`git status` before and after; never the implement stage's work),
commits the container as `mucli/job-<job12>-<binding>-repair<n>` (job-labelled,
removed with the job; the relay token and repair-only variables are reset, and
the image is secret-scanned), points the binding's job image (or
`metadata.runtime_repair_image` for the shared runtime) at it and re-runs the
checks with trigger `repair`. At most `jobs.repair.max_attempts` (2) per job,
each within `jobs.repair.max_cost_usd` ($3), `max_runtime_s` (1200) and
`max_iterations` (40); the spend is the cost-ledger stage `repair`. A failure,
timeout or exhausted budget is recorded and the job continues unverified; jobs
never park for missing tools or dependencies. Only commands the agent actually
ran count: a `REPAIR COMMANDS` line must match one of its shell calls. A repair
that ran no shell commands commits nothing (outcome `no_commands`, with the
commands it suggested in `error`). Records:
`metadata.environment_repairs[]` (`attempt, binding, trigger_suites, commands,
image, cost_usd, outcome, reverted_files, started_at, finished_at`, plus
`project_commands`, `registries`, `error`, `transcript`) and
`environment_repair` events (`phase` started / command / finished); the
transcript is the session view's **Repair** source. Two non-blocking commands
offer reuse: `addRepairToTemplate` (append the project-level commands to the
binding's template `job_setup`; templates only) and `saveRepairedTemplate`
(`{name}`, default `<template>-repaired`: a container template from the repaired
image with the binding's egress plus the registries used, and its setup).

## Surfaces

The terminal, web and Android show the same six-step pipeline and the same
server-provided actions. Job detail has the same tabs everywhere: **Session,
Plan, Diff, Review, Checks, Diagnostics, Cost & audit**. The web adds **Notes**
first, its default tab: the job's story in plain English (`notes` part, built
from events by `mu/jobs/core/notes.py`). The words come from the agents: plan and
review return `headline` and `notes` fields, and the implement stage ends its
final message with a `HEADLINE:` / `NOTES:` hand-off; without one, the first
sentence of the summary stands in. Setup includes Doctor as a
tab (web Jobs → Setup, Android Job settings → Setup, terminal `g u` and `g d`).

- Terminal: `/jobs` opens the keyboard dashboard. The `/job` subcommands are
  listed in `/help` and [commands.md](commands.md). Action names use
  lowercase hyphens (`approve-plan`, `save-plan-draft`, `restart-stage`,
  `mark-merged`). The camelCase ids are still accepted.
- Shared HTTP: `GET /api/jobs/vm/home`, `/vm/jobs`, `/vm/job/{id}` (optional
  `parts`); `POST /api/jobs/cmd` with `{type, job_id, args}`;
  `GET /api/jobs/events` (coalesced over 150 ms, naming the parts that changed).
  The state part carries the pending tool `approval` and a one-line
  `attention_headline`. The Cost & audit trail leaves out agent tool traffic and raw
  check output (`sandbox_event`, `checks_output`), so those events refresh only
  the session or checks part. The web loads the large `session` and `costs` parts
  only while their tab is open.
  Disabled commands return 409 with their reason. Malformed commands return 400.

## Storage

Each job's folder is `$MUCLI_HOME/jobs/<id>/`. It holds the worktree,
`ticket.json`, `attachments/`, versioned plans, `diff.patch`, `review.json`, check evidence,
stage transcripts and `costs.jsonl`. The job database is
`$MUCLI_HOME/jobs/jobs.sqlite3`, and job settings are
`$MUCLI_HOME/jobs/settings.json`. The store is created at the current schema;
older job stores are not supported (no migrations or backfills; start a fresh
`$MUCLI_HOME/jobs` if you have one). Every status event carries the job's core-state
snapshot. Compatibility endpoints pass lifecycle commands to the core. Raw status
and attention mutation endpoints return 410.
