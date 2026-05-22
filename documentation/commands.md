# Slash command reference

Every command available in the mucli REPL. Type `/help` in-session for a
one-line summary; this file is the full reference.

## Session lifecycle

| Command | Description |
| --- | --- |
| `/quit`, `/q` | Exit the REPL. |
| `/clear` | Clear the terminal screen. Does NOT touch history. |
| `/history` | Print the conversation history. |
| `/history clear` | Wipe the conversation history. |
| `/session` or `/session list` | List saved sessions in `~/.mucli/sessions/`. |
| `/session load <name>` | Load a saved session. |
| `/session new [name]` | Start a new session (interactive provider pick if a UI is attached). |
| `/session delete <name>` | Delete a saved session. |
| `/continue` | Resume the loop after a blocker or pause. |

## Provider, model, and prompt

| Command | Description |
| --- | --- |
| `/model [name]` | Show the active model, or switch with an argument. |
| `/provider <name>` | Switch provider (`openai`, `gemini`, `ollama`). |

## Mode toggles

| Command | Description |
| --- | --- |
| `/plan [on\|off\|toggle]` | Plan mode — read-only tools enforced; write tools blocked. |
| `/agentic` | Toggle tool-calling. With it off, the model only chats. |
| `/thinking` | Toggle extended thinking / reasoning where the provider supports it. |
| `/yolo` | Auto-approve write-side tools (`write_file`, `apply_diff`, `bash`, ...). |
| `/mode <name>` | Switch agent mode. See [Agent modes](#agent-modes). |

> Secret-path access (SSH keys, cloud creds, `.env*`, etc.) is blocked
> independently of `/plan` and `/yolo` by the always-on controls in
> [security_controls.md](security_controls.md). The session variable
> `security_allow_secret_paths` opts out for one session if needed.

### Agent modes

Valid values for `/mode`. Each has a dedicated doc covering the
workflow, tools, and quality bar.

| Mode | Description | Doc |
| --- | --- | --- |
| `default` | Baseline workflow: parallel reads, evidence-based verification, retrieval-aware. | [default_mode.md](default_mode.md) |
| `debug` | Reproduce-first debugging — deterministic repro, locate, bisect, fix, verify. | [debug_mode.md](debug_mode.md) |
| `feature` | Staged feature plan engine with explicit approval gates. | [feature_plan_engine.md](feature_plan_engine.md) |
| `research` | Exploration and explanation with citations and credibility weighting. | [research_mode.md](research_mode.md) |
| `loop` | Long-horizon autonomous loop with goal lock and timeline output. | [loop_mode.md](loop_mode.md) |
| `security` | Security audit gated on verified PoC + verified patch. | [security_mode.md](security_mode.md) |
| `teacher` | Structured course engine — diagnose → curriculum → per-lesson grade loop. | [teacher_mode.md](teacher_mode.md) |

## Files and workspace

| Command | Description |
| --- | --- |
| `/workspace` | Show attached workspace folders + staged files. |
| `/workspace folder <path>` | Attach a workspace folder. Accepts multiple paths. |
| `/workspace folder remove <path>` | Detach a workspace folder. |
| `/workspace folder clear` | Detach every workspace folder. |
| `/workspace file <path>` | Stage a file for the next user message. |
| `/workspace file` (no path) | List currently staged files. |
| `/workspace file clear` | Drop all staged file attachments. |
| `/workspace clear` | Drop everything (folders + staged files). |

## Tools and skills

| Command | Description |
| --- | --- |
| `/tool list` | Show all available tools with parameters and on/off state. |
| `/tool enable <name>` | Re-enable a previously disabled tool. |
| `/tool disable <name>` | Hide a tool from the model for this session. |
| `/skills` | List installed skills (compact). |
| `/skills <name>` | Show one skill's body and source path. |
| `/skills reload` | Clear the discovery cache and rescan. |
| `/skills enable <name>` | Re-enable a disabled skill. |
| `/skills disable <name>` | Hide a skill from the prompt for this session. |
| `/mcp` or `/mcp list` | List configured MCP servers with status. |
| `/mcp status` | Per-server tool count + capabilities reported in `initialize`. |
| `/mcp reload` | Re-spawn every MCP server from `.mu/mcp.json`. |
| `/mcp debug <server>` | Show recent stderr / error for one MCP server. See [mcp.md](mcp.md). |

## Memory and context

| Command | Description |
| --- | --- |
| `/memory status` | Show task memory + scratchpad stats and per-layer context fill (with global-cap total). |
| `/memory list <target>` | Inspect a store or a layer. Targets: `all`, `task`, `scratchpad`, `L1`, `L1B`, `L2`, `L3`, `L4`, `L4B`, `L5`. Tab-completes. |
| `/memory clear <target>` | Wipe a store. Targets: `task`, `scratchpad`, `all`. |

The collation buffer is drained by the model via the `flush` tool — there is no user-facing flush command.

### Pinning the session goal

Long-running multi-iteration turns routinely drift after the L2
conversation summary gets compacted mid-turn — the model loses sight
of what the user originally asked for. `/goal` pins the top-level ask
into L3 of every iteration's system prompt across **every** mode. L3
survives compaction because it's rebuilt from variables, not the
history list. The pin is also mirrored into `task_memory` with a
`goal:locked` tag for durable audit.

**Lifecycle**: the goal applies to the CURRENT turn — it auto-clears
at end of turn so a stale pin can't bias an unrelated next request.
Set a fresh goal at the start of each multi-step task.

| Command | Description |
| --- | --- |
| `/goal` | Show the current pinned goal (or "none pinned"). |
| `/goal <text>` | Set / replace the pinned goal. Bare form — no `set` keyword required. |
| `/goal set <text>` | Explicit set form. |
| `/goal clear` | Remove the pin before the turn ends. The task_memory audit trail entries stay. |
| `/goal show` | Show the current pinned goal (explicit form). |
| `/goal help` | Inline help. |

The agent can also self-pin via the `set_session_goal(goal, clear=False)`
tool — useful when the model detects a multi-step task and the user
forgot to run `/goal` manually. The tool description tells the model
to pin immediately on multi-step asks at the start of a turn. Since
the variable auto-clears at end of turn, the model doesn't need to
remember to `clear=true` for the common case.

## Documentation

| Command | Description |
| --- | --- |
| `/docs` | List every bundled doc under `documentation/` with a one-line summary. |
| `/docs <name>` | Render one doc (markdown). Name autocompletes on Tab; accepts the stem (`skills`) or filename (`skills.md`). |

## Variables

Every session variable from `utils/config.py:VARIABLE_SCHEMA` is settable
at runtime. See [configuration.md](configuration.md) for the full list.

| Command | Description |
| --- | --- |
| `/set <key> <value>` | Set a session variable. Type is validated/cast per schema. |
| `/set layer <id> <tokens>` | Shortcut for per-layer budgets — value is in **tokens** (matching the unit shown in `/memory`). Converted to chars at 4:1 internally. IDs autocomplete. L5 has no budget (tighten `context_token_limit` instead). |
| `/get <key>` | Print the current value of a variable. |
| `/get layer [<id>]` | Show one or all layer budgets in tokens (with underlying char value). |
| `/unset <key>` | Restore a variable to its default. |
| `/unset --all` | Restore all variables to defaults. |
| `/variables` | Show every variable and its current value. |

## Feature workflow

`/feature` manages persistent multi-phase feature plans. With no
argument, lists features. See [feature_plan_engine.md](feature_plan_engine.md)
for the engine model.

| Subcommand | Description |
| --- | --- |
| `/feature` or `/feature list` | List session features. |
| `/feature new <name>` | Create a new feature record. |
| `/feature load <id>` | Load an existing feature into the active slot. |
| `/feature delete <id>` | Remove a feature record. |
| `/feature exit` (alias: `unload`) | Clear the active feature without deleting it. |
| `/feature status` | Status of the active feature. |
| `/feature phases` | List phases of the active feature. |
| `/feature create` | Engine-driven creation of the next planning artifact. |
| `/feature show <task-id>` | Show a specific task. |
| `/feature move <task-id> <phase>` | Move a task between phases. |
| `/feature block <task-id> <reason>` | Mark a task blocked. |
| `/feature review` | Enter review loop for completed tasks. |
| `/feature archive <task-id>` | Archive a completed-and-reviewed task. |
| `/feature monitor` | Watch progress in real time. |
| `/feature help` | Inline help. |

## Teacher mode

`/teach` manages teacher-mode courses. Works from any mode (e.g. for
`/teach status` peeks); the agent only drives lessons while `/mode
teacher` is active. See [teacher_mode.md](teacher_mode.md) for the
engine model.

| Subcommand | Description |
| --- | --- |
| `/teach` or `/teach list` | List courses for this workspace. |
| `/teach new <subject>` | Create a new course. Doesn't auto-switch mode — run `/mode teacher` next. |
| `/teach load <id>` | Activate an existing course. |
| `/teach exit` (alias: `unload`) | Clear the active course without deleting it. |
| `/teach status` | Current module/lesson, progress %, average score. |
| `/teach next` | Next pending lesson hint. |
| `/teach grades` | Markdown table of every graded assignment. |
| `/teach curriculum` | Render the syllabus. |
| `/teach delete <id>` | Delete a course (irreversible). |
| `/teach help` | Inline help. |

## Research

| Command | Description |
| --- | --- |
| `/research <query>` | Run web search + synthesis as a one-shot. Uses the same research tools the model has available. |
| `/research status` | Source counts, by type, average credibility, current mode. |
| `/research sources [--type X] [--min N] [--query Q]` | List every source the research tools have registered with the citation engine. Each row has id, type, credibility (★), title, URL. Filters: `--type web\|academic\|social\|forum\|news\|documentation\|other`, `--min <0..1>`, `--query <substring>`. |
| `/research show <id>` | Full record for one source: title, URL, type, credibility score, authors, dates, metadata. |
| `/research bibliography` (also `biblio`/`bib`) | Compile the markdown bibliography block (`[^n]:` footnotes with credibility ratings) ready to paste into a report. |
| `/research stats` | Breakdown by source type + credibility-tier histogram. |
| `/research clear` | Wipe the citation engine. Useful when starting a new research topic. |

## Shell escape

| Command | Description |
| --- | --- |
| `/bash <cmd>` (aliases `/sh`, `/!`) | Run a shell command in the active workspace folder and print stdout/stderr. User-facing convenience — distinct from the agent's `bash` tool. Bounded by a 60s timeout. Not for interactive commands (vim, less); use Ctrl+Z to suspend mucli for those. |

## Diagnostics

| Command | Description |
| --- | --- |
| `/stats` | Runtime token counts, cost, mode/toggles, plus per-session tool & skill usage (top tools by call count + avg latency, every skill invoked, failed-call tally by error code). |
| `/stats clear` | Wipe the per-session usage tracker (counts, latencies, skill invocations, errors). Lifetime token counts and cost are kept — they represent real spend, not metadata. |
| `/help`, `/h` | List commands available in this session. |

## Provider-specific

| Command | Description |
| --- | --- |
| `/ollama <subcommand>` | Ollama-specific helpers (model listing, host check, etc.). |

## Notes

- Commands are matched case-sensitively at the leading `/`. Arguments
  are parsed by the command and may be case-insensitive (e.g. mode
  names, feature subcommands).
- All commands return a structured `CommandResult` internally, so
  external front-ends (`mucli` web UI, scripts piping JSON) get a
  consistent response shape.
- A command that mutates session state (e.g. `/plan on`, `/set ...`)
  takes effect immediately; subsequent model turns see the new state.
