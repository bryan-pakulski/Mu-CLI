# MUCLI for Neovim

MUCLI for Neovim turns the editor into a live frontend for the [MUCLI coding
agent](https://github.com/bryan-pakulski/Mu-CLI). It combines a persistent
conversation dock, deliberate context controls, native diff review, diagnostic
hints, inline completion, and editor tools the model can call.

This is an editor integration rather than an embedded web page. Buffers,
diagnostics, selections, extmarks, windows, and diffs all use native Neovim
APIs. The transport has no Lua plugin dependencies.

## What it does

- **Persistent chat dock** — streaming Markdown conversation plus a multiline
  composer. Closing the dock does not disconnect the event stream or prevent an
  approval/tool request from reaching Neovim.
- **Context v2** — the actual visible viewport and last-focused editor window,
  multiple extmark-backed pinned snippets across files, one-turn action context,
  LSP diagnostics, open-buffer metadata, changedticks, and unsaved text.
- **Truthful history** — source snapshots are a structured, turn-scoped payload;
  only a content-free context receipt enters conversation history. Old code can
  no longer masquerade as the current editor state on later turns.
- **Native code review** — request a focused review and receive navigable MUCLI
  diagnostics with explain, fix, and dismiss actions. Structured review and
  completion requests are history-free, so machine prompts never pollute chat.
- **Inline completion** — request a manual completion at the cursor, preview it
  as ghost text, accept all or one word, or dismiss it.
- **Safe diff review** — inspect original and proposed content in Neovim diff
  mode, move across a multi-file proposal, approve, reject, or return feedback.
  Changedtick and unsaved-buffer conflicts block stale edits.
- **Bidirectional editor tools** — the agent can read live buffers and
  diagnostics, inspect workspace state and symbols, navigate, publish review
  findings, and propose unsaved buffer edits.
- **Project sessions** — a stable session name is derived from the workspace,
  the workspace is attached automatically, and provider/model switching is
  scoped to that session.
- **Resilient transport** — dependency-free `curl` HTTP, a real incremental SSE
  parser, reconnect backoff, heartbeats, client-bound tool results, and durable
  history hydration.

## Requirements

- Neovim 0.10 or newer
- `curl`
- MUCLI running in GUI/server mode
- A configured MUCLI provider (`openai`, `gemini`, or `ollama`)

Start the backend from the repository root:

```sh
./mucli --gui
```

The default endpoint is `http://127.0.0.1:30311`.

## Installation

The plugin currently lives inside the MUCLI monorepo. Clone the repository,
then point your plugin manager at its subdirectory.

### lazy.nvim / AstroNvim

```lua
{
  dir = vim.fn.expand("~/src/Mu-CLI/extensions/mucli.nvim"),
  name = "mucli.nvim",
  config = function()
    require("mucli").setup({})
  end,
}
```

There are no required Neovim plugin dependencies. Treesitter-backed Markdown
highlighting is used automatically when your existing setup provides it.

### Native packages

Link or copy `extensions/mucli.nvim` beneath a `pack/*/start` directory, then
configure it in `init.lua`:

```lua
require("mucli").setup({})
```

On first use, MUCLI will load the workspace session or open a session/provider/
model picker when more information is needed.

## Recommended setup

```lua
require("mucli").setup({
  host = "http://127.0.0.1:30311",
  -- session = "my-project",       -- otherwise derived from the workspace
  -- provider = "openai",          -- only needed to create/override a session
  -- model = "your-model-name",

  yolo = false,                     -- keep server write approvals enabled
  workspace = {
    -- root = "/absolute/project", -- auto-detected from project markers
    allow_outside = false,
    allow_secret_paths = false,
  },
  window = {
    position = "right",
    width = 56,
    input_height = 7,
  },
  context = {
    automatic = true,
    max_chars = 48000,
    include_diagnostics = true,
    include_open_buffers = true,
  },
  hints = {
    enabled = true,
    max_items = 20,
    virtual_text = true,
  },
  completion = {
    enabled = true,
    context_lines = 60,
  },
})
```

Providing both `provider` and `model` makes them authoritative for the selected
session. Leave them unset to keep the session's saved provider configuration.

## Everyday workflow

1. Open `:Mucli` and write a multiline request in the composer.
2. Use visual `<leader>ms` or normal `<leader>mf` repeatedly to pin snippets or
   files. `:MucliContext` opens the context drawer; the exact live viewport is
   attached automatically.
3. Use `<leader>mc` for focused explain/improve/fix/review/test/doc actions.
4. Review file mutations in the native diff tab. Press `a` to approve, `r` to
   reject, or `e` to send corrective feedback.
5. Use `<leader>mh` for diagnostic hints or `<M-\>` for an inline completion.

Inside the composer:

| Key | Action |
| --- | --- |
| `Ctrl-s` | Send the multiline draft |
| `Ctrl-a` | Open the context drawer |
| `Ctrl-c` | Interrupt the active turn |
| `Ctrl-l` | Clear the draft |
| `q` (normal mode) | Close the dock |

Inside a diff:

| Key | Action |
| --- | --- |
| `a` | Approve the complete proposal |
| `r` / `q` | Reject and close |
| `e` | Reject with feedback for the agent |
| `]d` / `[d` | Next / previous proposed file |

## Default global keymaps

| Key | Action |
| --- | --- |
| `<leader>mm` | Toggle the MUCLI dock |
| `<leader>ma` | Ask MUCLI |
| `<leader>mc` | Open code actions |
| `<leader>ms` (visual) | Pin the exact selection; repeat across files |
| `<leader>mf` | Pin the active file |
| `<leader>mh` | Generate review hints |
| `<M-\>` | Request inline completion |
| `<M-l>` | Accept completion |
| `<M-e>` | Dismiss completion |
| `<leader>mx` | Interrupt the turn |
| `]m` / `[m` | Next / previous MUCLI hint |

Set any keymap to `false` or an empty string to disable it. The proof-of-concept
names `toggle_panel`, `send_visual`, and `send_file` remain accepted as aliases.

## Commands

| Command | Purpose |
| --- | --- |
| `:Mucli` | Toggle the conversation dock |
| `:MucliAsk [text]` | Ask directly or open a prompt |
| `:[range]MucliActions` | Open context-sensitive code actions |
| `:[range]MucliSend [text]` | Stage a range and ask about it |
| `:MucliSendFile [text]` | Stage the active file and ask |
| `:[range]MucliExplain` | Explain current/ranged code |
| `:[range]MucliImprove` | Improve current/ranged code |
| `:[range]MucliFix` | Fix current diagnostics or ranged code |
| `:[range]MucliReview` | Publish review hints as diagnostics |
| `:MucliHintsClear` | Clear MUCLI diagnostics |
| `:MucliHintAction` | Act on the nearest hint |
| `:MucliComplete` | Request an inline completion |
| `:MucliCompleteAccept [word]` | Accept all or one word |
| `:MucliCompleteDismiss` | Dismiss ghost text |
| `:MucliContext` | Open the live/turn/pinned context drawer |
| `:MucliContextAdd` | Pin a selection, file, or diagnostics |
| `:MucliContextInspect` | Inspect the exact structured payload before send |
| `:MucliContextClear[Turn/Pinned]` | Clear all, turn-only, or pinned context |
| `:MucliDiff` | Reopen the latest captured diff |
| `:MucliInterrupt` | Stop the active turn |
| `:MucliSetup` / `:MucliConfig` | Configure session/provider/model |
| `:MucliSession [name]` | Pick or switch session |
| `:MucliProvider [name]` | Pick or switch provider |
| `:MucliModel [name]` | Pick or switch model |
| `:MucliHealth` | Run the integration health check |

Use `:help mucli.nvim` for the concise in-editor reference.
The product priorities and acceptance target live in [ROADMAP.md](ROADMAP.md).

## Editor tools exposed to MUCLI

| Tool | Capability |
| --- | --- |
| `nvim_get_buffer` | Read authoritative live text, ranges, modified state, and changedtick |
| `nvim_list_buffers` | List loaded file buffers and editor state |
| `nvim_get_selection` | Read the latest explicit or currently active visual selection |
| `nvim_get_context_items` | Read every current turn and pinned item plus the live viewport |
| `nvim_get_diagnostics` | Read LSP and Neovim diagnostics |
| `nvim_get_workspace_state` | Read root, viewport, cursor, mode, LSP clients, and context summaries |
| `nvim_get_document_symbols` | Fetch document symbols asynchronously from LSP |
| `nvim_open_location` | Reveal a workspace file and location |
| `nvim_publish_diagnostics` | Publish structured findings in native diagnostic UI |
| `nvim_propose_edit` | Preview and apply a changedtick-guarded, unsaved buffer edit |

Registration is session- and client-bound. A stale editor stops contributing
tools after its heartbeat expires, and a replaced editor cannot answer the new
client's pending tool calls.

## Context model

MUCLI keeps editor state in deliberately separate lifetimes:

- **Live** is recomputed at send time from the last-focused normal editor window.
  It contains the real `w0`/`w$` viewport, cursor, diagnostics, and unsaved text.
- **Turn** is created by focused actions such as Explain/Fix and is consumed only
  after the server accepts that turn.
- **Pinned** is created with `<leader>ms`, `<leader>mf`, or `:MucliContextAdd` and
  persists until explicitly removed. Selection extmarks follow buffer edits and
  show a `changed` badge until refreshed.
- **Conversation** stores the user's request, assistant output, and a lightweight
  receipt containing paths/ranges/counts. Raw source snapshots never enter the
  retained history, goal, compaction, or durable-memory stream. Source-bearing
  editor tool calls/results expire to path/range receipts at the turn boundary.

When the payload reaches its budget, MUCLI preserves explicit one-turn context
first, then the live viewport, then persistent pins, and finally diagnostics.
Excluded content remains visible in the receipt instead of disappearing silently.

The drawer supports `<CR>` jump, `d` remove, `r` refresh, `a` add, `t` clear
turn-only context, `p` clear pins, and `i` inspect the exact outgoing payload.
Context receipts in chat are also inspectable with `<CR>`.

## Safety model

- `yolo` defaults to `false`; server-side file writes keep MUCLI's approval
  workflow.
- Editor-proposed changes are previewed before application and remain unsaved.
- MUCLI plan mode hides and blocks editor mutation tools while keeping live
  read-only context available.
- Open modified buffers are never overwritten after a changedtick conflict.
- Editor tool paths are restricted to the configured workspace by default,
  with existing symlinks resolved before the containment check.
- MUCLI's secret-path denylist and output redaction also apply to editor tool
  arguments and results.
- Tool calls continue to be handled while the chat dock is closed.

Only set `workspace.allow_outside = true` or `yolo = true` when that broader
authority is intentional. `workspace.allow_secret_paths = true` also opts the
session out of MUCLI's normal secret-path denylist; keep it disabled unless the
specific file access is deliberate.

## Health and troubleshooting

Run:

```vim
:checkhealth mucli
```

The check reports Neovim compatibility, `curl`, server reachability, workspace,
session, and editor-tool registration.

If connection fails:

1. Confirm `./mucli --gui` is running.
2. Confirm `host` matches the server bind address and port.
3. Run `:MucliSetup` to choose or create a provider-backed workspace session.
4. Run `:MucliHealth` and inspect the exact HTTP/registration error.

If a diff is blocked, return to the source buffer and reconcile unsaved changes;
the proposal intentionally will not overwrite content that changed after it was
generated.

## Development

The test runner is dependency-free apart from Neovim itself:

```sh
extensions/mucli.nvim/scripts/test.sh
```

It covers SSE fragmentation, HTTP parsing, unified diffs, structured hint and
completion payloads, conversation streaming, exact selections, path safety,
partial completion, the dock layout, and the command surface. CI runs it on
Neovim 0.10, 0.11, and 0.12, plus Python tests for the backend extension bridge.

## License

Same as MUCLI.
