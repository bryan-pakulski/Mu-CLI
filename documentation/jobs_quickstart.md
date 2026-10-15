# Jobs quickstart

A job turns a task into a reviewed draft PR. It plans, implements, runs checks
and reviews the change in disposable containers. You approve the plan and
decide when to push. The full reference is
[durable_engineering_jobs.md](durable_engineering_jobs.md).

## 1. Set up (once)

```
/job setup
```

The checklist checks Docker, your git identity, the job runtime image, the
controller, stage models and the optional Claude Code / Codex / Jira steps. Each
open step lists the command that fixes it, for example
`/job setup runtime build`. `/job doctor` re-checks everything in about a
second. On the web the same checklist is **Jobs → Setup**, with Doctor as a
tab. In the terminal dashboard, `g u` opens Setup and `g d` opens its Doctor tab.

## 2. Start a job

From any terminal session, or with no chat session at all:

```
/job Fix flaky login redirect --in ~/src/webapp
/job PROJ-123 --in ~/src/webapp
```

Free text becomes the job title. A Jira key imports that ticket (link Jira
first in Settings → Jira or in the JIRA setup step). Leave out `--in` to use
the current session's workspace. For more options, use
`/job create <title> --accept "criterion" --check "pytest -q" --agent plan=claude`.
On the web, use **New job**.

## 3. Approve the plan

The planner writes a plan with acceptance criteria and proposed checks, then
waits for you:

```
/job show <id>              # state, plan summary and the actions available now
/job approve <id>           # approves the latest plan version
/job replan <id> <feedback> # ask for a new plan instead
```

You can edit the plan (and add checks) in the dashboard, web or Android before
approving it.

## 4. Watch

```
/jobs                       # keyboard dashboard: Needs you, Running, Jobs
/job summary                # five lines per job
```

Writes inside the sandbox ask for approval unless the job runs unattended
(`--auto-approve`, or Settings → New job defaults). Pending approvals and
questions show up under **Needs you**. Answer with
`/job approve-tool <id>`, `/job deny-tool <id> [reason]` or `/job respond <id> <text>`.
Repo-declared images, environments and network access need approval too:
`/job approve-environment <id>` or `/job reject-environment <id> [reason]`.

Limits never cancel a job. When a job reaches its cost limit (default $100), a
stage budget, its runtime limit, a stage time limit or an iteration limit, the
running stage pauses where it is and the job shows up under **Needs you** as
"Limit reached: cost $100.03 / $100.00". Choose **Approve more & continue**
(the amount is prefilled; edit it) and the stage picks up its own conversation
again, or **Cancel job**. In the terminal:
`/job raise-limit <id> cost 25usd` (also `time 2h`, `tokens 5M`, `iterations 500`).
You can raise a limit the same way before it is reached.

## 5. Review

When the checks pass, the reviewer writes a report and the job stops at
**Review done**. The review never sends work back by itself.

```
/job diff <id>
/job reprompt <id> F-1,F-3 "also cover the logout path"
/job recheck <id>           # after your own edits in the job worktree
```

## 6. Push and open the PR

```
/job push <id>
```

Push needs a passing check run for the current commit. Mucli squashes the work
into one commit (ticket key plus a `Mucli-Job` trailer), pushes the branch and
opens a **draft** PR. The job is then **Pushed**.

Mucli checks the PR every `publish.merge_poll_seconds` (default 300 s; `0` =
off). When the PR merges, the job moves to **Done** on its own. When the PR is
closed without merging, the job needs you: choose **Re-push** to open the PR
again, or **Mark done** to close the job out. `/job mark-merged <id>` is the
manual override, for example when merge polling is off or the host cannot be
queried.

Costs are capped at $100 per job by default (`defaults.max_cost_usd`). Leave
the New job cost field blank to use that default, or enter `0` for no cap. All job settings: `/job settings` or
[configuration.md](configuration.md#job-settings).

## 7. Run a job in a template

A job normally runs in the shared runtime image (Python and Node, no project
tools). To run planning, implementation, checks and review in an image that
already has your project's toolchain, build and tune a container on
**Environments** (`/containers`) and save it as a **template**. Then choose it
when you create the job:

- **Web:** the New job sheet's **Run in** picker lists Default runtime, your
  templates (size, GPU, job setup) and your environments. **Advanced…** opens
  the multi-environment editor (monorepo paths, Docker, GPUs). Tick
  **Remember for this workspace** to make the template the repository's
  default (a Settings → Toolchains row), which scheduled Jira imports also use.
  The Jira import dialog and each Jira watcher query have the same **Run in**
  choice.
- **Terminal:** `/job create "Fix login" --in ~/src/app --template rsq-node-build --remember`,
  or `/job jira pull PROJ-1 --template rsq-node-build`. The jobs dashboard asks
  **Run in** when you create a job.

When a template can't reach a registry your lockfiles need, the picker warns
`◆ can't reach registry.npmjs.org`; **Allow** adds the domain to the template's
network allowlist.

**Job setup** installs the repository's dependencies into each job's worktree,
once per job, before planning. Add the commands on the template card's **Used
for jobs** section (or `/container template setup rsq-node-build add "npm ci"`),
for example:

```
npm --prefix resq-management ci
cargo fetch --manifest-path stream-processor-rs/Cargo.toml
```

Setup runs in the job image with the template's network allowlist (model hosts
dropped), the worktree read-write, the shared `/cache`, no credentials and the
usual stage isolation. Its output appears in the job's Activity. When a command
fails, the job waits for you: **Retry**, edit the template, or **Continue
without setup**. Setup runs again after **Refresh environments** and when a job's
images were cleaned while it sat idle. Edits to a template affect only jobs
started afterwards.

**Unverified checks.** A check that could not run (a missing command, a tool
from `node_modules` that isn't installed, a registry it couldn't reach) is
**unrunnable** (`?`), never a pre-existing failure. The job stops as
**unverified** and names the commands, the cause and the fix. **Continue
unverified** reviews it anyway: the verdict is capped at *needs work* and
pushing needs an override reason.
