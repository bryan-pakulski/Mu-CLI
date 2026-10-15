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

Jobs run unattended: stages never stop to ask you. Implement and checks
approve their own tool calls inside the sandbox, plan and review deny writes
automatically, and an agent's questions are answered with "proceed with your
best judgement and state the assumption". Each automatic decision appears in the
job's session as an `auto_permission` / `auto_answered` line. **Needs you** lists
the pipeline gates (plan approval, push) and paused jobs (limits, failures).

A failed template job setup, checks that could not run, or an unapproved
repository image do not stop the job either: setup failure shows a warning,
unverified checks go on to review (verdict capped at needs work, and the PR body
says the checks could not run), and an unapproved repository environment is
skipped in favour of the next source. Approve it for future jobs with
`/job approve-environment <id>` (or `/job reject-environment <id> [reason]`).

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

**Where the PR / MR opens.** GitHub, GitLab, Bitbucket and Gitea/Forgejo are
supported. The host kind comes from Settings → Push → PR provider hosts
(`host=gitea`), else the host name, else the Publish mode setting, else
automatic detection: a self-hosted Gitea or Forgejo is recognised from its API,
even when it requires sign-in. Gitea drafts open with a `WIP:` title prefix.

**Tokens.** Opening the PR/MR needs a token for that host:
`GITEA_TOKEN`, `GITLAB_TOKEN`, `GITHUB_TOKEN` (or `GH_TOKEN`) or
`BITBUCKET_TOKEN`. Set it in the environment before starting mucli, or store it
in Settings → Push → PR / MR tokens or with `/job publish token gitea <token>`
(saved 0600 under `~/.mucli/jobs/`, used only by the host-side push, never
inside a job container). `/job publish` shows which tokens are set. For Gitea,
create the token under Settings → Applications with `write:repository` scope.
If the token is missing, the branch is still pushed and the job asks you to add
the token and push again; the push dialog warns about this beforehand.

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
fails, the job does not wait for you: it records the failure, shows a **Job
setup failed** warning and continues to planning (checks that need the missing
dependencies then report unverified). Fix the template, then **Refresh
environments** to run setup again. Setup also runs again when a job's images were
cleaned while it sat idle. Edits to a template affect only jobs started
afterwards.

**Unverified checks.** A check that could not run (a missing command, a tool
from `node_modules` that isn't installed, a registry it couldn't reach) is
**unrunnable** (`?`), never a pre-existing failure. The run is recorded as
**unverified**, naming the commands, the cause and the fix, and the job goes on
to review without stopping. The review is told the checks could not run and its
verdict is capped at *needs work*. Pushing needs no override; the PR body starts
with "⚠ Checks could not run (unverified): <tools/causes>".
