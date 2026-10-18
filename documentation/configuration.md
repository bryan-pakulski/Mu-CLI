# Configuration reference

This document covers every configuration surface mucli exposes:

- Command-line flags
- Environment variables and provider keys
- Configuration files under `.mu/`
- Workspace-context files
- Session variables (runtime knobs)

## Command-line flags

Invoke via `python mucli.py [flags]`.

| Flag | Description |
| --- | --- |
| `--model <name>` | Default model name to start with. |
| `--provider <anthropic\|gemini\|ollama\|openai>` | LLM provider. |
| `--session <name>` | Load a saved session non-interactively. |
| `--workspace <path>` | Attach a workspace folder. Repeatable. |
| `--yolo` | Start in YOLO mode (auto-approve writes). |
| `--debug` | Verbose logging. |
| `--system <prompt>` | Override the initial system instruction. |
| `--gui` | Launch the browser GUI in the background (default port 30311). |
| `--trace` | Launch the GUI and open the Trace Analyzer dashboard (`/trace`). See [tracing.md](tracing.md). |
| `--trace-analyze <file>` | Print a terminal summary of a trace JSONL file and exit (headless quick-look). See [tracing.md](tracing.md). |

## Environment variables

| Variable | Used by | Description |
| --- | --- | --- |
| `OPENAI_API_KEY` | OpenAI provider | API key for OpenAI models. |
| `ANTHROPIC_API_KEY` | Anthropic provider | API key for Claude models. `ANTHROPIC_BASE_URL`, `ANTHROPIC_TIMEOUT_SECONDS`, `ANTHROPIC_MAX_TOKENS`, `ANTHROPIC_AUTO_CACHE=0` and `ANTHROPIC_EAGER_TOOL_STREAMING=1` tune the client. |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini provider | API key for Google Gemini. |
| `OLLAMA_HOST` | Ollama provider | URL of the Ollama server (also settable via `/set ollama_host`). |
| `MUCLI_HOME` | Filesystem layout | Base directory for sessions and logs. Defaults to `~/.mucli/`. |

Sessions and logs live under `MUCLI_HOME`:

- `~/.mucli/sessions/` — saved session JSON files
- `~/.mucli/logs/` — log output
- `~/.mucli/trace/` — per-run JSONL traces (one file per run; see [tracing.md](tracing.md))
- `~/.mucli/secrets.env` — agent-harness secrets (`CLAUDE_CODE_OAUTH_TOKEN`, optionally
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`), mode 0600; set with `/job agents secret KEY value`
  or Settings → Agents. Environment variables win over this file.

### Job agent harnesses

Each job stage (plan, implement, review) can run on mucli, Claude Code or Codex
(`/job create … --agent plan=claude,implement=codex:gpt-5.5`, or the New job sheet). Defaults,
binaries, per-stage models and the credential source live in Job settings under `harness.*`
(`/job settings`). `/job agents` shows install and credential status. External agents run in the
same sandboxed stage container with the host CLI mounted read-only and one copied credential;
rebuild the runtime image after upgrading mucli (`python3 -m mu.jobs.sandbox.build`). See
[plans/agent_harnesses.md](plans/agent_harnesses.md) for the approval model and limitations.

### Jobs Setup and Doctor

`/job setup` (web: Jobs → Setup; Android: Job settings → Setup; dashboard: `g u`) is a planos-style
checklist: Docker, git identity, the job runtime image, the job controller, mucli stage models,
Claude Code, Codex, default agents, the sandbox self-test, JIRA, Docker-in-Docker and GPUs. Every
step checks the machine live and offers the actions that fix it (`/job setup <step> <action>`),
e.g. `/job setup runtime build`, `/job setup claude paste-token`, `/job setup sandbox selftest`,
`/job setup codex test`. Choices and self-test results live in `~/.mucli/jobs/setup.json`.

Doctor is a tab of Setup on every surface (dashboard: `g d` opens it directly, `tab` switches).
`/job doctor` reports everything a job run depends on as ok / warn / fail in about a second;
`/job doctor --deep` also runs the sandbox self-test. The self-test starts real stage containers
for mucli and every ready external agent (plan and implement) and checks from inside: no
capabilities, seccomp, read-only paths, loopback only, egress limited to the agent's provider, and
only that agent's credential visible. The agent test stages make one small model request each.

The runtime image is labelled with a fingerprint of the code stages execute; the doctor flags the
image as stale whenever mucli's Python code or prompts change, until it is rebuilt.

### Job settings

Job run rules live in `$MUCLI_HOME/jobs/settings.json` (default
`~/.mucli/jobs/settings.json`). Change them with `/job settings set <key> <value>`,
reset them with `/job settings reset <key>`, or use Jobs → Settings on the web
or Android, or `g s` in the terminal dashboard. Blank means no value: the
default, or no limit. Two top-level keys sit outside the rule sections.
`plan_approval` (default on) stops every job at Plan review. `stage_models`
holds the provider and model for each stage (`plan`, `implement`, `review`).
Repository overrides go in `.mucli/jobs.toml` (`[[environments]]`,
`[[checks.suites]]`); see
[durable_engineering_jobs.md](durable_engineering_jobs.md).

The table below is generated from the settings schema: run
`python3 -m mu.jobs.settings_doc --write` after changing it (a test fails while
the table is stale).

<!-- job-settings:begin (generated from mu/jobs/job_settings.py RULES_SCHEMA) -->
**Model budget**

| Key | Default | Description |
| --- | --- | --- |
| `sandbox.max_tokens` | 50,000,000 tokens | Token budget per stage. Tokens one stage may use across its runs in a job; then the job pauses and asks you to approve more or cancel. |
| `sandbox.count_cache_reads` | off | Count cache reads. Off: only new tokens (uncached input + output) count. On: every re-sent context token counts, which exhausts long stages quickly. |
| `sandbox.max_cost_usd` | 50 USD | Cost budget per stage. Estimated API spend one stage may reach across its runs in a job; then the job pauses and asks you to approve more or cancel. |
| `limits.review_patch_chars` | 60,000 characters | Review patch in prompt. Largest diff embedded in the review prompt; larger diffs list the remaining files for the reviewer to read. |

**New job defaults**

| Key | Default | Description |
| --- | --- | --- |
| `defaults.max_retries` | 2 | Repair retries. Automatic re-attempts after a failed stage. |
| `defaults.max_cost_usd` | 100 USD | Job cost limit. Total spend for the whole job (every stage, run and fix attempt). Reaching it pauses the job and asks you to approve more or cancel. 0 = no cap (stage budgets still apply). |
| `defaults.max_runtime_seconds` | — | Job runtime limit. Active (worker) time for the whole job. Reaching it pauses the running stage and asks you to approve more time or cancel. Blank = none. |

**Agent iterations**

| Key | Default | Description |
| --- | --- | --- |
| `limits.max_iterations` | 1,000 | Default iterations per stage. Applies to every stage without a more specific limit. |
| `limits.stage_max_iterations.plan` | — | Plan stage iterations. Blank = default above. |
| `limits.stage_max_iterations.implement` | — | Implement stage iterations. Blank = default above. |
| `limits.stage_max_iterations.review` | — | Review stage iterations. Blank = default above. |
| `jobs.stage_effort.plan` | — | Plan stage effort. Model effort (thinking) per request. Lower is faster. |
| `jobs.stage_effort.implement` | — | Implement stage effort. |
| `jobs.stage_effort.review` | — | Review stage effort. |

**Stage time limits**

| Key | Default | Description |
| --- | --- | --- |
| `sandbox.model_request_timeout_s` | 3,600 seconds | Model request timeout. How long one model call from inside the sandbox may take. |
| `sandbox.tool_timeout_s` | 3,600 seconds | Tool command timeout. Default limit for one shell command the agent runs inside a stage (when it doesn't set its own). |
| `sandbox.stage_timeout_s.plan` | 3,600 seconds | Plan. |
| `sandbox.stage_timeout_s.implement` | 3,600 seconds | Implement. |
| `sandbox.stage_timeout_s.review` | 3,600 seconds | Review. |
| `sandbox.stage_timeout_s.checks` | 3,600 seconds | Checks. |

**Checks**

| Key | Default | Description |
| --- | --- | --- |
| `checks.max_fix_attempts` | 2 | Automatic fix attempts. Times a check failure is sent back to the implementer before a human is asked. |
| `checks.baseline_triage` | on | Baseline triage. Re-run failing checks on the base commit; failures that also happen there are reported, not sent back to the implementer. |
| `checks.command_timeout_s` | 3,600 seconds | Validation command timeout. Per-command limit for host-run validation commands (legacy verifier). |
| `checks.suites` | — | Required check suites. Added to every job's plan checks. Commands run in the sandboxed checks stage. |

**Toolchains**

| Key | Default | Description |
| --- | --- | --- |
| `sandbox.toolchains` | — | Repository toolchain images. Base image = an image that already has the project's toolchain (e.g. rust:1.88 or the project's CI image), template:<name> or environment:<name> (a container-mode environment). Paths limit the row to repository-relative components (comma-separated; blank = whole repository). Docker = yes gives the job a private Docker engine (needs Sysbox). npm, Cargo/crates.io, Maven/Gradle and PyPI registries are allowed by default (Extra egress domains); add any other registry the build needs there. |

**Build environments**

| Key | Default | Description |
| --- | --- | --- |
| `environments.use_session_environment` | on | Use the session's environment. A job created from a container session runs in a snapshot of that session's environment. |
| `environments.inherit_egress` | on | Inherit environment egress. Implement and checks may reach the environment's egress allowlist (model-provider hosts are always dropped). |
| `environments.home_exclude_extra` | — | Extra home excludes. Paths under /root (globs allowed) never copied into a job snapshot, in addition to secrets, caches and history. |
| `environments.probe_env_extra` | — | Extra probed variables. Environment variables kept from the environment's login shell, in addition to PATH and the toolchain homes. |
| `environments.secret_scan_allow` | — | Allowed secret-scan paths. Paths (globs allowed) the post-build secret scan accepts, e.g. /root/.config/tool/test.pem. |
| `jobs.environment_retention_h` | 72 hours | Idle environment retention. How long a failed or waiting job keeps its build environment images. Kept jobs are exempt; images are re-created on retry. |
| `sandbox.gpus_enabled` | on | GPUs for jobs. Off: no job container gets a GPU, whatever its build environment requests. |
| `sandbox.docker_engine_image` | `docker:dind` | Docker engine image. Image of the per-job Docker engine, run under the Sysbox runtime. |
| `sandbox.docker_storage_gb` | 20 GB | Docker engine storage cap. Space a job's Docker engine may use for images, containers and volumes. |

**Sandbox**

| Key | Default | Description |
| --- | --- | --- |
| `sandbox.image` | `mucli-job-runtime:latest` | Runtime image. Local image; it is never pulled. |
| `sandbox.cpu` | 4 | CPUs. |
| `sandbox.memory` | `8g` | Memory limit. Docker memory value, e.g. 8g or 16384m. |
| `sandbox.pids_limit` | 4,096 | Process limit. Most processes and threads one stage or check container may run (builds and parallel test runners need many). |
| `sandbox.code_overlay` | on | Run current job code. Mount this mucli's job code read-only over the copy baked into each stage image, so images built by an older mucli run the current stage worker. Off: stages run the image's own copy. |
| `sandbox.cache_max_gb` | 20 GB | Dependency cache cap. Shared package caches reused across jobs; least recently used entries are pruned. 0 = unlimited. |
| `jobs.container_cli` | `auto` | Container CLI. Auto uses Docker when present, else Podman. Podman cannot run the per-job Docker engine. |
| `sandbox.egress_extra` | `registry.npmjs.org`, `registry.yarnpkg.com`, `crates.io`, `index.crates.io`, `static.crates.io`, `static.rust-lang.org`, `repo.maven.apache.org`, `repo1.maven.org`, `services.gradle.org`, `downloads.gradle.org`, `plugins.gradle.org`, `plugins-artifacts.gradle.org`, `pypi.org`, `files.pythonhosted.org`, `pythonhosted.org`, `cdn.playwright.dev`, `playwright.download.prss.microsoft.com`, `playwright.azureedge.net` | Extra egress domains. Domains implement, checks and template job setup may reach in addition to the model relay. Defaults to the npm, Cargo/crates.io, Maven/Gradle and PyPI registries; clear the list to lock jobs down. |

**Push**

| Key | Default | Description |
| --- | --- | --- |
| `publish.timeout_s` | 3,600 seconds | Push timeout. Limit for git push and the PR/MR request. |
| `publish.mode` | — | Publish mode. Used when a job does not set its own mode. |

**Agent harnesses**

| Key | Default | Description |
| --- | --- | --- |
| `harness.default.plan` | `mucli` | Plan agent. |
| `harness.default.implement` | `mucli` | Implement agent. |
| `harness.default.review` | `mucli` | Review agent. |
| `harness.claude.binary` | — | Claude Code binary. Blank = ~/.local/bin/claude or claude on PATH. |
| `harness.claude.auth` | `auto` | Claude credential. Copied into the stage sandbox. Auto: ANTHROPIC_API_KEY, then a `claude setup-token` token, then your ~/.claude login. |
| `harness.claude.models.plan` | `claude-opus-5-5` | Claude plan model. |
| `harness.claude.models.implement` | `claude-sonnet-5-5` | Claude implement model. |
| `harness.claude.models.review` | `claude-opus-5-5` | Claude review model. |
| `harness.codex.binary` | — | Codex binary. Blank = ~/.local/bin/codex or codex on PATH. |
| `harness.codex.auth` | `auto` | Codex credential. Copied into the stage sandbox. Auto: your ~/.codex login (auth.json, as Codex itself prefers), then OPENAI_API_KEY. |
| `harness.codex.models.plan` | — | Codex plan model. Blank = Codex's configured model. |
| `harness.codex.models.implement` | — | Codex implement model. |
| `harness.codex.models.review` | — | Codex review model. |

**Automatic retries**

| Key | Default | Description |
| --- | --- | --- |
| `retry.backoff_base_s` | 30 seconds | First retry delay. Doubles on every further attempt. |
| `retry.backoff_max_s` | 600 seconds | Longest retry delay. Cap for the doubling delay. |

**Scheduling & retention**

| Key | Default | Description |
| --- | --- | --- |
| `jobs.max_concurrent` | 5 | Concurrent jobs. Jobs that may run a stage at the same time. |
| `jobs.max_stage_crash_retries` | 2 | Stage crash retries. Crash retries per stage before a human is asked. |
| `jobs.drift_check_interval_s` | 300 seconds | Base drift check interval. How often finished jobs are checked against their base branch. 0 = off. |
| `jobs.retention_days` | 30 days | Archive retention. Artifacts of archived jobs older than this are purged. 0 = keep forever (env MUCLI_JOBS_RETENTION_DAYS wins when set). |

**Git & PR hosts**

| Key | Default | Description |
| --- | --- | --- |
| `publish.push_remote` | `origin` | Push remote. Remote the job branch is pushed to. |
| `publish.pr_remote` | `origin` | PR remote. Remote whose host receives the PR/MR (also the base branch's remote). Differs from the push remote for forks. |
| `publish.provider_hosts` | — | PR provider hosts. Self-hosted PR providers as host=kind (kind: github, gitlab, bitbucket, gitea), e.g. git.acme.dev=gitea. Self-hosted Gitea/Forgejo is also detected automatically; tokens are set below (PR / MR tokens). |
| `publish.run_hooks` | off | Run commit-msg hook. Run the repository's commit-msg hook on the squashed publish commit (checkpoints never run hooks). |
| `publish.delete_remote_branch` | off | Delete remote branch on archive. Archiving a pushed, merged or cancelled job also deletes its remote branch (the local branch is always deleted). |
| `publish.merge_poll_seconds` | 300 seconds | PR merge check interval. How often pushed jobs check whether their PR was merged (merged → done; closed → needs you). 0 = off. |
| `jobs.git_timeout_seconds` | 120 seconds | Git command timeout. Limit for one local git command on a job repository (raise for very large repositories). |
| `jobs.clone.submodules` | on | Clone submodules. Initialise submodules when a job clones a repository by URL and in every job worktree. |
| `jobs.clone.lfs` | `auto` | Git LFS. auto: run git lfs pull when the repository uses LFS and git-lfs is installed (otherwise warn). |

**Environment repair**

| Key | Default | Description |
| --- | --- | --- |
| `jobs.repair.enabled` | on | Repair the environment. Off: checks that can't run are reported unverified straight away. |
| `jobs.repair.max_attempts` | 2 | Repair attempts per job. |
| `jobs.repair.registries` | `auto` | Repair egress. Auto: the build environment's allowlist plus the package registries detected for the repository, the OS package mirrors and toolchain installers. Template: the allowlist plus mirrors and installers only. Model-provider hosts are always dropped. |
| `jobs.repair.max_cost_usd` | 3 USD | Cost budget per repair. Counts toward the job's cost. |
| `jobs.repair.max_runtime_s` | 1,200 seconds | Time limit per repair. |
| `jobs.repair.max_iterations` | 40 | Iterations per repair. |
<!-- job-settings:end -->

## Configuration files

### `.mu/hooks.json` — lifecycle hooks

Shell-command hooks fired at five lifecycle points:

```json
{
  "hooks": [
    { "name": "log-tool-calls", "point": "post_tool",
      "command": "echo $MU_TOOL_NAME >> /tmp/tools.log" },
    { "name": "deny-rm-rf", "point": "pre_tool",
      "command": "case \"$MU_TOOL_ARGS_JSON\" in *rm\\ -rf*) exit 1;; *) exit 0;; esac",
      "on_failure": "short_circuit",
      "message": "rm -rf detected; refuse" }
  ]
}
```

Hook points: `pre_provider_call`, `post_provider_call`, `pre_tool`,
`post_tool`, `on_stop`. Hooks at the same point run in priority order
(lower first). A non-zero exit at `pre_tool` with
`on_failure: short_circuit` denies the call.

### `.mu/skills/<name>/SKILL.md` — workspace skills

Workspace-local skills shadow built-ins of the same name. See
[skills.md](skills.md).

### `.mu/CONTEXT.md` — workspace context

Optional plain-text file auto-loaded into LAYER 1 of the system prompt.
Use it for project-specific instructions that should always be in
context.

## Session variables

Every variable below is settable at runtime via `/set <key> <value>` and
inspectable via `/get <key>` or `/variables`. Defaults come from
`utils/config.py:VARIABLE_SCHEMA`.

### Agent behavior

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `agent_mode` | str | `default` | Active agent mode. Same set as `/mode`. |
| `strict_mode` | bool | `false` | Force user approval for every tool call. |
| `yolo` | bool | `false` | Auto-approve write-side tools. |
| `max_iterations` | int | `1000` | Hard cap on iterations per conversation turn. When reached mid-work, the agent runs one final consolidation turn (tools disabled) and writes a handoff summary to memory instead of stopping silently. |
| `session_goal` | str | `""` | The user's pinned top-level task for the current turn, rendered in L3 every iteration so the model keeps direction even when L2 is compacted. Set with `/goal <text>`; clears at end of turn unless `session_goal_sticky` is in effect. |
| `session_goal_sticky` | bool | `false` | When true (or in `loop`/`feature` mode, which default to sticky), `session_goal` is **not** cleared at end of turn — it persists across turns in L3 until cleared (`/goal clear`) or replaced. Long-horizon multi-turn work needs the goal to survive turn boundaries; conversational default-mode use does not. The `session_goal_sticky_explicit` tracker records whether the user set this via `/set`, so the mode-aware default only applies until overridden. |
| `reflective_retry_enabled` | bool | `true` | Show retryable tool failures + remediation hints inline. |
| `streaming_enabled` | bool | `true` | Stream tokens one-by-one instead of one final panel. |
| `structured_tool_results` | bool | `true` | Use structured envelope for tool results (vs. raw text). |
| `trace_enabled` | bool | `true` | Write a per-run JSONL trace of every iteration to `~/.mucli/trace/` (context layers, real vs estimated tokens, compactions, nudges, tools, subagents, memory). The Trace Analyzer dashboard (`/trace`, or `mucli --trace`) visualizes it. Set `false` to disable. See [tracing.md](tracing.md). |

### Context — global cap

The system prompt mucli sends each turn is composed of seven layers
(see [the Memory guide](memory_guide.md)). Each layer has its own soft
budget, and `context_token_limit` is the **hard global cap** the
provider actually sees. The compactor subtracts the active size of all
non-L5 layers from the cap before deciding how much room L5
(conversation history) gets — so a heavy `AGENTS.md` or many expanded
skills will trigger compaction sooner.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `context_token_limit` | int | `480000` | **Global** token cap (sum of all 7 layers + response reserve). Capped further by the provider's real context window if smaller. Changing this reratios the per-layer char budgets proportionally (see [Per-layer budgets](#per-layer-budgets)). |
| `context_trim_threshold` | float | `0.85` | Fraction of the cap above which compaction kicks in. |
| `response_token_reserve` | int | `4096` | Tokens reserved for the model's reply. Tune down for small-context models (Ollama 8k). |
| `auto_compaction_enabled` | bool | `true` | Provider-aware fallback for model-led context management. Clear eligible old tool results first, then summarize if pressure remains. Saved `false` values remain off; provider-overflow recovery always remains enabled. |
| `auto_compaction_token_limit` | int | `0` | Optional smaller threshold for projected L5 after selective clearing. `0` permits the full provider-aware budget. A positive value retains the half-threshold cleanup target; the default fallback targets 80% of its trigger. Recent/protected results can keep history above the target. Saved nonzero settings are respected; use `/set auto_compaction_token_limit 0` to remove a previous 64k limit. |
| `tool_result_floor` | int | `4` | Trailing tool-result messages in the active turn that compaction (including emergency compaction) must leave verbatim (R3). Prevents mid-turn compaction from dropping results just received. Mode-aware: `loop`/`feature` modes raise this to at least 8 (long-horizon work re-covers more files). |
| `tool_result_cache_entries` | int | `50` | Max entries in the tool-result sidecar cache (`recall()` + auto-recall by locator). Mode-aware: `loop`/`feature` modes raise this to at least 256. |
| `tool_result_cache_bytes` | int | `524288` | Max bytes in the tool-result sidecar cache. Mode-aware: `loop`/`feature` modes raise this to at least 2 MB. |
| `emergency_keep_recent` | int | `2` | Trailing messages kept verbatim by emergency (pre-flight) compaction — smaller than the normal keep-recent so budget is reclaimed fast; `tool_result_floor` still protects recent tool results. |
| `compact_history` | bool | `false` | Remove completed-turn tool metadata after a response. Disabled by default to preserve inspectable history. |
| `progress_checkpoint_every` | int | `0` | Optional periodic L2 progress checkpoint: every N iterations, fold recent history into the structured `conversation_summary` **without compacting** (the anchor doesn't advance and entries stay in L5). `0` disables in every mode. Model-maintained checkpoints avoid extra periodic summary requests by default; set a positive cadence to opt in. |
| `recoverage_stall_threshold` | int | `4` | Context-gathering stall detection: number of consecutive iterations that re-read files already read this turn **without** a concrete change (write/bash/spawn) before a "stop gathering, act" re-orient nudge is injected. `0` disables. Catches the diffuse re-coverage stall that doesn't form a clean repeated/periodic tool sequence. |

### Provider retry (transient failures)

When the provider returns a transient error (429, 503, timeout,
connection reset), mucli backs off exponentially. The retry loop is
bounded by a cumulative-wait budget rather than a fixed retry count so
real outages get meaningful backoff without infinite stalling.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `provider_retry_max_total_wait_seconds` | float | `120.0` | Cumulative time budget across all retries for one provider call. Bounds worst-case stall on a flapping endpoint. |
| `provider_retry_base_delay` | float | `0.4` | Initial sleep after the first transient failure. Doubles each attempt (with jitter). |
| `provider_retry_max_delay` | float | `30.0` | Cap on any single sleep — backoff stops doubling here. |
| `provider_max_retries` | int | `30` | Safety belt against pathological 0-delay loops; the budget is normally the binding constraint. |

Inspect live usage with `/memory` — every layer is shown with its
current token count and per-layer cap, plus a **TOTAL** row against
`context_token_limit`.

### Per-layer budgets

Each layer's budget is a soft cap on how much of *that* content gets
included on a turn. All are settable via `/set` and visible in
`/variables`.

The five char-budget variables below **scale proportionally with
`context_token_limit`** (`utils/config.py:compute_layer_char_budgets`):
at the reference limit of `480000` each lands on its target token
budget × 4 chars, and it never drops below an absolute floor (the
historical minimum). So the "default" column is the value at the
default `context_token_limit = 480000`; lowering the global cap
shrinks these toward their floors, and raising it grows them.

| Variable | Type | Default | Floor | Layer | Description |
| --- | --- | --- | --- | --- | --- |
| `context_files_max_chars` | int | `20000` | `4000` | **L1A** | Workspace context files (AGENTS.md/CLAUDE.md/MUCLI.md/.mu/CONTEXT.md). Whole-file-or-skip, no truncation. `0` disables L1A. |
| `skills_max_chars` | int | `40000` | `6144` | **L1B** | AVAILABLE SKILLS block (compact index + auto-expanded bodies). `0` disables skills entirely. |
| `skills_mode` | str | `compact` | — | **L1B** | `compact` (index + auto-expand on trigger) or `full` (every body inlined up to the budget). |
| `conversation_summary_char_limit` | int | `80000` | `24000` | **L2** | Rolling conversation summary. Clipped from the tail when exceeded. |
| `active_goal_context_char_limit` | int | `16384` | `4000` | **L3** | Feature/task status + scratchpad snapshot. |
| `retrieval_context_char_limit` | int | `40000` | `10000` | **L4B** | Semantic-retrieval snippets injected for the current turn. |
| `retrieval_top_k` | int | `5` | — | **L4B** | Number of semantic-retrieval hits to consider when assembling L4B. |

**L4** (recent tool activity) and **L5** (conversation history) have no
per-layer char budget of their own. L4 is retained verbatim; the agent manages
cleanup with `context_status`, `checkpoint_progress`, and `compact`, while the
hard context ceiling remains the safety backstop.
summary; L5 gets whatever the global cap minus the response reserve and
the other layers leaves over. Neither is settable via `/set layer`.

#### Layer-budget shortcuts

Layer IDs are easier to remember than the underlying variable names.
Both `/set` and `/get` accept a `layer` subcommand:

```
/set layer L1 6000         # 6000 tokens; stored as 24000 chars in
/get layer L1              # tokens + underlying chars
/get layer                 # table of all five layer budgets
```

The value is in **tokens** — matching the unit shown in `/memory` and
the splash banner — and is converted to chars at a 4:1 ratio for the
underlying `_chars` variable. (Setting the variable directly in chars

Layer IDs autocomplete on Tab. Valid IDs are `L1A`, `L1B`, `L2`, `L3`,
`L4B`. `L4` and `L5` are rejected — L4 is agent-managed and L5 is the
global-cap remainder; use `context_status` / `compact` or adjust
`context_token_limit` instead.

### Notes

- All char-budgets are **soft caps**: the layer is truncated to that
  many chars before being injected into the prompt, then re-counted in
  tokens (via `tiktoken`) for the per-layer fill display and the
  global-cap accounting.
- Tightening any per-layer budget frees room for L5. Loosening one
  steals from L5 — you'll see compaction fire sooner.
- `/set <name> 0` typically disables a layer (subject to `min(1, ...)`
  floors in the renderer). To disable a *file-list*-style layer, set
  the corresponding `*_files` variable to an empty string.

### Memory and scratchpad

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `memory_enabled` | bool | `true` | Enable persistent task memory. |
| `memory_max_entries` | int | `64` | LRU cap on task-memory entries. |
| `memory_summary_limit` | int | `8` | Memory entries shown in the system prompt summary. |
| `scratchpad_enabled` | bool | `true` | Enable turn-local scratchpad. |
| `scratchpad_max_entries` | int | `24` | Max scratchpad entries before eviction. |
| `scratchpad_persist_across_turns` | bool | `false` | Keep the scratchpad across turns. Mode-aware: `loop`/`feature` modes persist regardless; `default`/`teacher` clear at turn start unless this is `true`. |

### Collation

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `collation_enabled` | bool | `true` | Allow deferring a batch of read-side tool results into the collation buffer (drained with `flush`). |
| `collation_batch_share` | float | `0.10` | A batch is deferred only when its combined size exceeds this share of the model's real context window (capped below); smaller batches come back in the same turn. |
| `collation_batch_cap_tokens` | int | `24000` | Cap for the collation threshold. |
| `tool_result_inline_budget` | int | `4000` | Tokens of a tool result kept inline; larger results become an observation + stored ref. Capped at `tool_result_inline_share` of the window. |
| `tool_result_inline_share` | float | `0.05` | Window share that caps the inline budget (protects small local models). |

### Speed and effort

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `reasoning_effort` | str | `""` | Effort for agent turns (`/effort`): `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`; empty = provider default. Mapped per provider (Anthropic effort, OpenAI `reasoning_effort`, Ollama `think`). |
| `turn_roll_deferred` | bool | `true` | Summarize a finished turn on a background thread and merge it at the next turn start instead of blocking the end of the turn. |
| `small_window_profile` | str | `auto` | `auto`: compact tools/schemas, no skills index and capped context files when the model's real window is ≤ `small_window_threshold`; `on`/`off` force it. |
| `small_window_threshold` | int | `32768` | Window size (tokens) at or below which the small-window profile applies. |
| `openai_api` | str | `auto` | `auto`: the Responses API for OpenAI reasoning models (encrypted reasoning replayed between tool-loop turns); `chat` / `responses` force one API. |
| `openai_prompt_cache_retention` | str | `""` | e.g. `24h` for models that support extended prompt-cache retention. |
| `openai_xhigh_models` | list | `[]` | OpenAI model prefixes that accept `reasoning_effort: xhigh` (`/effort max`); others get `high`. |

### Loop mode

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `loop_active` | bool | `false` | Whether loop mode is currently engaged. |
| `loop_features` | str | `""` | JSON list of features created during the current loop run. |
| `loop_detection_enabled` | bool | `true` | Detect and break tight repeat loops. |
| `loop_detection_repeat_threshold` | int | `5` | Repeat count that trips (tight) pattern-based loop detection. |
| `loop_detection_periodic_max_period` | int | `6` | Soft knob (not in the canonical schema, but settable via `/set` and read with `int()`): max spacing, in tool calls, between two equal tool-name sequences for the *periodic* loop detector (R7) to flag a repeat. Lower = stricter. |
| `retryable_escalation_threshold` | int | `3` | Soft knob (settable via `/set`, read with `int()`): when a tool hits the same retryable error this many times in one turn with different args (which evades pattern detection), inject an escalation telling the model to change approach (R8). |

### Ollama tuning

Ollama runs a model one of three ways: **directly on ollama.com** (`ollama_mode cloud`), **through
the local daemon to a `:cloud` model** (detected from the tag or `/api/tags` `remote_host`), or
**locally**. Local models always get one explicit, constant `num_ctx` of at least 64k (capped by
the model's trained window): a missing or changing value makes the daemon reload the model (tens
of seconds) and its default is often only 4096. Cloud routes never send `num_ctx` or `keep_alive`.
Each extra `OLLAMA_NUM_PARALLEL` slot on the daemon costs another `num_ctx` of KV memory; mucli
does not serialize subagents itself. Generation parameters below: `0` means "server default".

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `ollama_host` | str | `""` | Ollama server URL. Falls back to `OLLAMA_HOST`. |
| `ollama_num_ctx` | int | `0` | Explicit context size (sent on every request). `0` = automatic: `ollama_num_ctx_default` for local models, nothing for cloud. |
| `ollama_num_ctx_default` | int | `65536` | Automatic local context size (never below 65536; capped by the trained window). |
| `ollama_keep_alive` | str | `""` | Sent only when set (e.g. `30m`, `-1`); empty lets the daemon's `OLLAMA_KEEP_ALIVE` apply. |
| `ollama_request_timeout` | float | `0` | Read timeout; `0` = 900 s local (prompt processing streams nothing), 300 s cloud. A local first-token timeout fails with advice instead of being retried. |
| `ollama_side_model` | str | `""` | Model for summaries/chunking so side calls never evict the main local model's KV cache. |
| `ollama_token_safety_factor` | float | `1.3` | Seed divisor for the compaction limit until the learned token drift takes over. |
| `ollama_num_predict` | int | `0` | Max tokens to predict. |
| `ollama_temperature` | float | `0.0` | Sampling temperature. |
| `ollama_top_p` | float | `0.0` | Nucleus sampling cutoff. |
| `ollama_top_k` | int | `0` | Top-K sampling cutoff. |
| `ollama_repeat_penalty` | float | `0.0` | Repetition penalty. |
| `ollama_seed` | int | `0` | RNG seed (`0` = random). |
| `ollama_mirostat` | int | `0` | Mirostat sampling mode (`0`/`1`/`2`). |

## How configuration is layered

1. **CLI flags** (`--provider`, `--model`, `--workspace`, `--yolo`, ...)
   set initial state for the session.
2. **Environment variables** provide credentials and host URLs.
3. **`.mu/*.json` files** declare durable infrastructure (hooks) and are
   loaded per-workspace.
4. **Session variables** (`/set`) override defaults at runtime and
   persist with the session via `/list` / `/load`.

Variables and saved sessions live under `MUCLI_HOME` (default
`~/.mucli/`).
