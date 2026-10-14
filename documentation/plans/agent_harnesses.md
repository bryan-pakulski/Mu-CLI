# Plan: choose the agent per job stage — mucli, Claude Code or Codex

Status: implemented (2026-10-14) · Owner: jobs · Reference: planos (`.planos_review/planos`,
Claude Code only), Claude Code 2.1.299 and codex-cli 0.162.1 on this machine.

## Decisions (2026-10-14)

1. **Per stage.** A job launch asks which agent runs each agent stage (plan, implement,
   review): `mucli`, `claude` (Claude Code) or `codex` (OpenAI Codex CLI), with a model per stage.
   Defaults come from setup (Settings → Agent harnesses). Checks never use an agent.
2. **Credentials are copied into the sandbox, planos-style.** Exactly one credential per harness
   is mounted read-only into the stage container and copied into its tmpfs home; the agent can read
   it. Claude order: `ANTHROPIC_API_KEY` → `CLAUDE_CODE_OAUTH_TOKEN` (env or mucli secrets, from
   `claude setup-token`) → `~/.claude/.credentials.json`. Codex: `~/.codex/auth.json` (as Codex
   itself prefers its login) → `OPENAI_API_KEY`. Settings can pin one source per harness. Setup warns that a refreshed OAuth token inside the
   sandbox can invalidate the host login (planos README), recommending a setup token / API key.
3. **Codex approvals.** `codex exec` cannot relay per-tool approvals. Implement runs
   `--dangerously-bypass-approvals-and-sandbox` (the job container is the sandbox) when the job
   auto-approves writes, otherwise `--approve-for-me` (Codex's own automatic approval review).
   **Verified limitation:** Codex's own sandbox (bubblewrap) cannot create user namespaces inside
   the job container (`--cap-drop ALL`, no-new-privileges, default seccomp), and the Landlock
   fallback (`features.use_legacy_landlock`) still goes through bwrap. So `-s read-only` would make
   every shell read fail: plan/review instead run unsandboxed *inside* the container's read-only
   workspace mount. With `--approve-for-me` every command first fails Codex's sandbox and is then
   sent to the auto-reviewer for an unsandboxed rerun; in a live probe it refused to run a project
   script ("contents cannot be inspected"). Setup and `/job agents` warn about this and recommend
   auto-approve (or Claude Code / mucli) for Codex implement stages. `approval_policy=untrusted`
   is no longer supported by codex 0.162.
4. **Claude approvals** follow mucli's gate: `--permission-prompt-tool stdio --permission-mode manual`
   (without `manual`, user/project settings can auto-allow edits; verified live); with auto-approve (or
   a read-only stage, which also gets `--disallowed-tools Edit Write NotebookEdit`) it adds
   `--dangerously-skip-permissions`. Otherwise each `can_use_tool` control request goes to the job's
   approval flow: the stage parks (needs input) with the tool call, and after the human approves
   it resumes the Claude session (`--resume`) and the matching request is allowed.

## Design

- **Where it runs:** the existing stage container (`--network none`, read-only rootfs, host
  uid:gid, cap-drop ALL, job worktree mount, egress via the Unix-socket bridge). The host CLI binary
  is bind-mounted read-only (`claude` is glibc-dynamic → Debian-based images; `codex` is
  static-musl). The entrypoint is `mu.jobs.sandbox.harness_worker` (Python, already in every job
  image): it starts the local egress proxy, copies the credential into a tmpfs `CLAUDE_CONFIG_DIR`
  / `CODEX_HOME`, runs the CLI, translates its event stream into the same `sandbox_event` lines,
  `result.json` and `transcript.json` the mucli worker writes, so the rest of the job pipeline (UI,
  cost, review parsing, approvals, resume) is unchanged.
- **Egress:** a harness stage's allowlist adds its provider hosts (Claude: `api.anthropic.com`,
  `statsig.anthropic.com`; Codex: `api.openai.com`, `chatgpt.com`, `auth.openai.com`) on every
  stage, plus the job's normal extras for implement.
- **Session continuity:** each harness gets a per-job state directory in the job artifacts
  (`harness/<agent>/`), linked into the CLI's config dir for transcripts/sessions only (never the
  credential). Paused stages resume the CLI session (`claude --resume <id>`,
  `codex exec resume <thread>`), like mucli stages resume their own transcript.
- **Commands:**
  - Claude: `claude -p --input-format stream-json --output-format stream-json --verbose
    --permission-prompt-tool stdio [--dangerously-skip-permissions] [--disallowed-tools …] --model M
    [--effort E] [--resume ID] --append-system-prompt <stage system> [--json-schema <review schema>]`,
    prompt as a stream-json user frame on stdin; env `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`,
    `DISABLE_AUTOUPDATER=1`, `BASH_DEFAULT_TIMEOUT_MS`/`BASH_MAX_TIMEOUT_MS` = tool timeout.
  - Codex: `codex exec --json --skip-git-repo-check -C /workspace [-m M] [-c
    model_reasoning_effort=E] <sandbox/approval flags> [--output-schema <file>] -` (prompt on stdin;
    system text prepended), or `codex exec resume <thread> …`.
- **Output:** Claude stream-json (`system/init` session id, `assistant` text/tool_use, `user`
  tool_result, `result` with `total_cost_usd`, `usage`, `is_error`, `structured_output`,
  `rate_limit_event`) and Codex JSONL (`thread.started`, `item.started/completed`: `agent_message`,
  `command_execution`, `file_change`, `reasoning`…, `turn.completed` usage, `turn.failed`, `error`).
  Usage-limit messages park the job with the reset time.
- **Setup:** Settings section "Agent harnesses" (defaults per stage, binaries, models, auth
  source), a status/doctor view (binary + version + credential kind + expiry), and a secret slot for
  `CLAUDE_CODE_OAUTH_TOKEN` in `$MUCLI_HOME/secrets.env` (0600, never returned by the API).
- **Launch:** web New job sheet, Android and `/job create --agent plan=claude,implement=codex`
  ask per stage; the job page shows the agent per stage.

## Tests

Unit: argument builders per harness/stage/approval mode, event parsers (recorded Claude and Codex
streams from this machine), credential resolution and copy, approval round trip with a fake
Claude script, usage-limit parsing, settings/status/API shapes, `missing_stages` skips harness
stages. Docker integration (opt-in): a fake CLI mounted in a real stage container.

## Implementation (2026-10-14)

- `mu/jobs/harness.py` — per-stage agent resolution, binaries/versions, credentials, `secrets.env`,
  status, host-side mounts and session-state copy. `mu/jobs/harness_cli.py` — command lines,
  control-protocol frames, stream translation to mucli history/UI events, usage-limit detection.
- `mu/jobs/sandbox/harness_worker.py` — in-container worker; `HarnessRelay` (model_relay.py) answers
  `/interaction` and records `/usage` against the stage cost/token budget (Claude's
  `total_cost_usd` replaces running estimates; Codex's model is read from its session rollout).
- Runner: provider hosts join the stage egress allowlist; `container_args(harness_mounts=, module=)`
  accepts only `/opt/harness/{claude,codex}`, `/harness/creds` (read-only) and `/harness/state`.
  Session ids are kept in `harness/<agent>/<stage>.json`; paused stages resume that session.
- Missing binary/credential pauses the stage with a fix-it message.
- Settings section `harness.*`; `GET /api/jobs/agents`, `PUT /api/jobs/agents/secret`;
  `/job create --agent …`, `/job agents [secret KEY value]`; summary VM `stage_agents`.
- The runtime image must be rebuilt (`python3 -m mu.jobs.sandbox.build`) to include the worker.
- Verified: unit tests (`tests/test_job_harness.py`), Docker tests with fake CLIs including the
  approval park → approve → resume round trip (`tests/test_job_harness_docker.py`), and live plan
  stages with real Claude Code 2.1.299 (Haiku) and codex-cli 0.162.1 through the egress bridge.

## Sandbox verification (2026-10-14)

`tests/test_job_harness_sandbox_docker.py` checks every harness (mucli, Claude Code, Codex) for plan
and implement on a real stage container, from both sides:

- `docker inspect`: `--network none`, read-only rootfs, not privileged, `CapDrop ALL`,
  `no-new-privileges`, host uid:gid, exact mount set and modes (workspace read-only except
  implement, `/workspace/.git`, `/artifacts`, `/relay`, `/harness/creds`, `/opt/harness/*` read-only;
  `/output`, `/harness/state` writable), no credentials in the container environment, the right
  worker module.
- Inside, as the agent process: CapEff/CapPrm 0, NoNewPrivs 1, seccomp filter active; rootfs,
  artifacts, relay, credentials, CLI binary and `.git` pointer unwritable; only `lo`, no direct
  internet or DNS; the egress proxy allows only the harness's own provider hosts (403 otherwise; no
  proxy at all for mucli without extra egress); only the one copied credential is in the agent's
  environment; a copied login file is 0600.

The same probe was run live through real Claude Code (Haiku) and Codex (gpt-6.1-sol, ChatGPT login)
for plan and auto-approved implement: all checks passed. A live gated Claude implement parked with
`approval_required` on the exact `Bash` call.

## "Message agent" on harness stages (2026-10-14)

`HarnessRelay` serves `/messages` (the stage's `stage_message` events with ids). The worker includes
undelivered messages in the stage prompt and tracks delivered ids in the harness state, so a resume
never repeats one. Claude Code: new messages are written into the running session as user turns;
Claude folds a mid-turn message into the current turn (one `result`), so the worker runs with
`--replay-user-messages` and closes stdin once every written turn has been echoed (falls back to
result counting, plus a 120s idle close). Codex takes no input mid-turn: when its turn ends the
worker resumes the thread (`codex exec resume`) with the new messages. Verified with Docker fakes and
live with Claude Code (a message sent during a 25s command was followed in the final reply).
