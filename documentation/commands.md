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
| `/blocker` (alias `/blockers`) or `/blocker list` | List open blockers the agent raised with `raise_blocker` (id, mode, summary, what it needs). |
| `/blocker resolve <id> [answer]` | Resolve one blocker with an optional answer. Sending your next chat message also resolves every open blocker, with the message as the answer. |

## Provider, model, and prompt

| Command | Description |
| --- | --- |
| `/model [name]` | Show the active model, or switch with an argument. |
| `/provider <name>` | Switch provider (`openai`, `gemini`, `ollama`). |
| `/prompts` (alias `/prompt`) | List every system prompt (base and each mode) and where it comes from. |
| `/prompts reload` | Clear the prompt cache to pick up file edits. |
| `/prompts init [name\|all]` | Write template files to `~/.mucli/prompts/` for editing. |
| `/prompts show <name>` | Print the effective prompt text. |
| `/prompts validate [name]` | Check that the critical tool anchors are present. |
| `/prompts edit <name>` | Open the override file in `$EDITOR` (or print its path). |
| `/prompts diff <name>` | Compare an override with the bundled prompt. |
| `/prompts keep <name>` | Keep an override older than the bundled prompt and silence its notice until the bundled version moves again. |
| `/prompts reset <name>` | Move the override aside (`<name>.md.bak`) so the bundled prompt applies. |

The bundled templates in `mu/prompts/templates/` are the single prompt source. When a user override's `version` is older than the bundled template, the terminal and GUI show a one-time notice pointing at `diff`, `keep` and `reset`; the override keeps working.

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
| `debug` | Debug cases — engine-checked repro, hypotheses, fix verification, root cause. | [debug_mode.md](debug_mode.md) |
| `feature` | Feature plans — user-approved draft, evidence-backed criteria, review, user completion. | [feature_plan_engine.md](feature_plan_engine.md) |
| `research` | Exploration and explanation with citations and credibility weighting. | [research_mode.md](research_mode.md) |
| `loop` | Long-horizon work on a user-owned mission with increments, budgets and done-when checks. | [loop_mode.md](loop_mode.md) |
| `security` | Security audit gated on verified PoC + verified patch. | [security_mode.md](security_mode.md) |
| `teacher` | Structured course engine — diagnose → learner-approved curriculum → gated lesson loop. | [teacher_mode.md](teacher_mode.md) |

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

## Memory and context

| Command | Description |
| --- | --- |
| `/memory status` | Show task memory + scratchpad stats and per-layer context fill (with global-cap total). |
| `/remember <text> [--scope repository] [--kind decision] [--pin]` | Store explicit durable memory immediately. Model-managed writes use same ledger automatically and do not request approval. |
| `/memory list durable [query]` | Browse cross-session memory in current personal/workspace/repository/branch scopes. |
| `/memory search <query>` | Search durable memory without changing recall counters or freshness. |
| `/memory show <uuid>` | Show record, provenance, immutable revisions and event timeline. |
| `/memory why [last|receipt-id]` | Explain exactly which memories were injected or skipped and why. |
| `/memory edit <uuid> <statement>` | Create a new immutable revision. |
| `/memory pin|unpin|archive|restore|forget <uuid>` | Curate lifecycle. Forget purges content and derived indexes, leaving a content-free tombstone. |
| `/memory timeline|graph <uuid>` | Inspect audit history or one-hop relationships. |
| `/memory policy` | Show automatic capture, recall budget, receipt and scope policy. |
| `/memory list <target>` | Inspect a store or a layer. Targets: `all`, `task`, `scratchpad`, `L0`, `L1`, `L1B`, `L2`, `L3`, `L5` (L4/L4B have no inspectable slab). Tab-completes. |
| `/memory list saved` | List cross-session memory snapshots saved with `/memory save`. |
| `/memory clear <target>` | Wipe a store. Targets: `task`, `scratchpad`, `all`. |
| `/memory clear saved` | Delete every saved memory snapshot. |
| `/memory save <name>` | Legacy/export command: snapshot task memory to JSON under `~/.mucli/memory/`. |
| `/memory load <name>` | Legacy/import command: merge task-memory JSON into current session. Eligible entries promote automatically on next turn. |

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

**Sticky goals (long-horizon work)**: in `loop` and `feature` modes the goal
is **sticky** — it does *not* auto-clear at end of turn and persists across
turns in L3 until you clear it (`/goal clear`) or set a new one. Long-horizon
multi-turn work needs the goal to survive turn boundaries. In `default`
mode the goal clears per turn unless you opt in with
`/set session_goal_sticky true` (and `/unset session_goal_sticky` reverts to
the mode-aware default). Setting `session_goal_sticky` via `/set` records an
explicit tracker so the mode default yields to your explicit choice; `/unset`
clears that tracker.

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
the variable auto-clears at end of turn (unless sticky), the model
doesn't need to remember to `clear=true` for the common case.

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
| `/set layer <id> <tokens>` | Shortcut for per-layer budgets — value is in **tokens** (matching the unit shown in `/memory`). Converted to chars at 4:1 internally. IDs autocomplete. Valid IDs: `L1`, `L1B`, `L2`, `L3`, `L4B`. `L4` and `L5` have no per-layer budget (tighten `context_token_limit` instead). |
| `/get <key>` | Print the current value of a variable. |
| `/get layer [<id>]` | Show one or all layer budgets in tokens (with underlying char value). |
| `/unset <key>` | Restore a variable to its default. |
| `/unset --all` | Restore all variables to defaults. |
| `/variables` | Show every variable and its current value. |

## Debug cases

`/debug` works on the session's debug cases (debug mode). Changes made
with the command are recorded with the actor `user:cli`. See
[debug_mode.md](debug_mode.md).

| Subcommand | Description |
| --- | --- |
| `/debug` or `/debug status` | The current case: title, status, symptom, accepted reproducer, latest hypotheses, suspects, recent check runs and the next step. |
| `/debug cases` | Every case with its status; `*` marks the current one. |
| `/debug select <id>` | Make a case current. |
| `/debug close <root cause>` | Close the current case once it is `verified`; saves the root cause to the Memory Ledger and reports a gap when no regression test was recorded. |
| `/debug unreproducible <why>` | Close the current case as unreproducible (not allowed once verified). |

## Feature workflow

`/feature` manages the session's feature plans: the user approves, the
agent executes. With no argument, lists features. User actions go
through the same engine gates as the panel and mobile and are recorded
with the actor `user:cli`. See
[feature_plan_engine.md](feature_plan_engine.md).

| Subcommand | Description |
| --- | --- |
| `/feature` or `/feature list` | List session features. |
| `/feature new <name>` | Create a new feature record. |
| `/feature load <id>` | Load an existing feature into the active slot; the agent resumes rather than re-plans. |
| `/feature delete <id>` | Remove a feature record. |
| `/feature exit` (alias: `unload`) | Clear the active feature without deleting it. |
| `/feature status` / `/feature phases [<id>]` | Print the plan: approval, tasks, criteria and evidence. |
| `/feature approve` | Approve the draft; records you, the time and the plan hash. The model cannot approve. |
| `/feature start <task>` | Start a task (one in progress at a time). |
| `/feature move <task> <status> [reason]` | The same gated move as the panel. |
| `/feature attest <task> <criterion> <why>` | Mark a criterion met without a run, recorded as a user attestation. |
| `/feature done <task> <reason>` | Complete a task whose criteria are not all met, recorded as an override. |
| `/feature diff <proposal> approve\|reject [reason]` | Decide a diff proposal. |
| `/feature review` | List what still blocks completion. |
| `/feature complete` | Mark the feature complete; needs every task completed and reviewed, no open blockers and a passing suite run after the last change. |
| `/feature help` | Inline help. |

## Loop missions

`/loop` manages the loop mission. You own the mission; the agent works
it. Pause, resume and stop take effect at the agent's next iteration
boundary. See [loop_mode.md](loop_mode.md).

| Subcommand | Description |
| --- | --- |
| `/loop` or `/loop status` | Goal, status, budget left, done-when criteria, last increment and backlog. |
| `/loop start <goal> [--done "<criterion>" [--check "<cmd>"]]... [--max-increments N] [--max-cost USD] [--max-hours H] [--max-minutes M] [--max-turns N]` | Start a mission and switch to loop mode. `--check` attaches a command that must exit 0 to the preceding `--done`. Refused while another mission is running, paused or blocked. |
| `/loop pause [reason]` | Pause the mission. |
| `/loop resume` | Resume a paused mission, or extend a finished one. Refused while a budget is used up. |
| `/loop stop [reason]` | End the mission; a stopped mission cannot resume. |
| `/loop goal <text>` | Change the goal, recorded with you as the actor. |
| `/loop budget --max-cost USD --max-hours H ...` | Raise or set budgets. |
| `/loop attest <criterion id> <why>` | Mark a done-when criterion met without a run, recorded as an override. |

## Teacher mode

`/teach` (alias `/t`) manages teacher-mode courses. Works from any mode
(e.g. for `/teach status` peeks); the agent only drives lessons while
`/mode teacher` is active. Learner decisions go through the same engine
gates as the panel and mobile and are recorded with the actor
`user:cli`. See [teacher_mode.md](teacher_mode.md) for the engine model.

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
| `/teach approve` | Approve the proposed curriculum (the learner's consent; the model cannot approve). |
| `/teach start <lesson_id> --override <reason>` | Start a lesson out of order; the reason is recorded. |
| `/teach finish --early <reason>` | Record an early finish so the course can be finalised before every lesson has passed. |
| `/teach delete <id>` | Delete a course (irreversible). |
| `/teach help` | Inline help. |

## Research

| Command | Description |
| --- | --- |
| `/research <query>` | Switch to research mode and send the query as a research request. |
| `/research new <query>` | Clear the research trail, then run the query. |
| `/research status` | Questions, claims, sources by state (candidate / read / cited), gaps, current question. |
| `/research questions` | The question tree with status (open / answered / contested / dropped), claim and origin counts. |
| `/research claims [qid]` | Recorded claims (all, or one question and its sub-questions) with confidence, contested flag and sources. |
| `/research gaps` | Questions without claims, contested and low-confidence claims, unread candidates worth reading. |
| `/research sources [--state S] [--type X] [--min N] [--query Q] [--topic T]` | Sources, read and cited by default. `--state candidate\|read\|cited`, `--type web\|academic\|social\|forum\|news\|documentation\|other`, `--min <0..1>` credibility, `--query` title/URL substring, `--topic` question substring. |
| `/research show <n\|sN>` | One source by citation number or candidate id: state, origin, credibility, authors, dates, questions. |
| `/research bibliography [--cited] [qid]` (also `biblio`/`bib`) | The markdown bibliography: cited sources grouped by root question, then read-but-uncited ones under "Consulted" (`--cited` drops those). |
| `/research stats` | Sources read, independent origins, average credibility, counts by state and type, claim confidence. |
| `/research topic [name]` | Show the current question, or select (creating if needed) the root question `name`. |
| `/research topics` | The question groups that have read sources, with counts. |
| `/research export <dir>` | Write `questions.md`, `source_register.md` and `evidence_log.md` from the ledger (workspace and container sessions; workspace bounds apply). |
| `/research clear` | Wipe the session's research trail: sources, questions and claims. |

## Shell escape

| Command | Description |
| --- | --- |
| `/bash <cmd>` (aliases `/sh`, `/!`) | Run a shell command in the active workspace folder and print stdout/stderr. User-facing convenience — distinct from the agent's `bash` tool. Bounded by a 60s timeout. Not for interactive commands (vim, less); use Ctrl+Z to suspend mucli for those. |

## Diagnostics

| Command | Description |
| --- | --- |
| `/stats` | Runtime token counts, cost, mode/toggles, plus per-session tool & skill usage (top tools by call count + avg latency, every skill invoked, failed-call tally by error code). |
| `/stats clear` | Wipe the per-session usage tracker (counts, latencies, skill invocations, errors). Lifetime token counts and cost are kept — they represent real spend, not metadata. |
| `/verbose [on\|off\|toggle]` | Toggle verbose rendering. When OFF (default) the UI hides tool-arg dumps, per-turn token lines, result previews, the compaction notice, and the user-echo panel; the compact `→ tool_name` indicator stays. |
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


### Security scans

`/security [status|scan <title>|list|open <id>|scope [JSON]|coverage|report]` manages the
session's audit. `/security runner host` explicitly enables real-host PoCs for that scan;
`/security runner docker` or `auto` returns to container execution. Each PoC still requires
human command/script review. See [Security mode](security_mode.md).
