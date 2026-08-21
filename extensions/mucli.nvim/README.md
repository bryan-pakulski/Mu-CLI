# mucli-neovim

Neovim extension for [mucli](https://github.com/bryanp/Mu-CLI) — AI coding agent side-panel chat with treesitter highlighting, visual selection context, side-by-side diff accept/reject, and custom neovim tools.

Built for **AstroVim** and any lazy.nvim-compatible Neovim config. Requires Neovim 0.10+.

## Features

- **Side-panel chat** — vertical split, treesitter markdown highlighting, streaming SSE responses
- **Visual selection context** — send highlighted code to the agent with `<leader>ms`
- **Active file context** — send current file with `<leader>mf`
- **Side-by-side diff view** — native `diff` mode, accept/reject hunks with `<leader>da` / `<leader>dr`
- **Custom neovim tools** — agent can open files, jump to lines, read buffers, get visual selection, apply diffs via extension tool dispatch
- **Model selection** — `:MucliModel` to switch provider/model from within neovim
- **Session lifecycle** — explicit session config required, auto-registers extension with mucli backend

## Requirements

- [mucli](https://github.com/bryanp/Mu-CLI) running with GUI server (`mucli --gui` or `mucli gui`)
- [plenary.nvim](https://github.com/nvim-lua/plenary.nvim) — for HTTP client via `plenary.curl`
- [nvim-treesitter](https://github.com/nvim-treesitter/nvim-treesitter) — for markdown highlighting in chat buffer (optional but recommended)
- Neovim 0.10+

## Installation

### lazy.nvim

```lua
{
  "bryanp/Mu-CLI",
  dir = "/path/to/Mu-CLI/extensions/mucli.nvim",
  -- or use the repo directly:
  -- url = "https://github.com/bryanp/Mu-CLI",
  -- subdirectory = "extensions/mucli.nvim",
  dependencies = {
    "nvim-lua/plenary.nvim",
    "nvim-treesitter/nvim-treesitter",
  },
  config = function()
    require("mucli").setup({
      session = "my-session",
      provider = "openai",      -- optional, defaults to session's current provider
      model = "gpt-4o",         -- optional, defaults to session's current model
      host = "http://localhost:30311",  -- optional, defaults to localhost:30311
      window = {
        width = 60,
        position = "right",     -- "right" or "left"
      },
      keymaps = {
        send_visual = "<leader>ms",
        send_file = "<leader>mf",
        toggle_panel = "<leader>mt",
        interrupt = "<leader>mi",
        accept_hunk = "<leader>da",
        reject_hunk = "<leader>dr",
      },
    })
  end,
}
```

### AstroVim

Add `~/.config/nvin/lua/plugins/mucli_spec.lua`

```lua
return {
  "bryanp/Mu-CLI",
  -- Change this path to the absolute path where your Mu-CLI repo is cloned
  dir = "<PATH_TO_REPO>/extensions/mucli.nvim", 
  dependencies = {
    "nvim-lua/plenary.nvim",
    "nvim-treesitter/nvim-treesitter",
  },
  config = function()
    require("mucli").setup({
      window = {
        width = 60,
        position = "right",
      },
      keymaps = {
        send_visual = "<leader>ms",
        send_file = "<leader>mf",
        toggle_panel = "<leader>mt",
        interrupt = "<leader>mi",
        accept_hunk = "<leader>da",
        reject_hunk = "<leader>dr",
      },
    })
  end,
}
```

### Manual (packer.nvim)

```lua
use {
  "bryanp/Mu-CLI",
  dir = "/path/to/Mu-CLI/extensions/mucli.nvim",
  requires = {
    "nvim-lua/plenary.nvim",
    "nvim-treesitter/nvim-treesitter",
  },
  config = function()
    require("mucli").setup({
      session = "my-session",
    })
  end,
}
```

## Configuration

### Interactive Setup (Recommended)

If you call `setup()` without a `session` option, the plugin automatically
launches an interactive setup wizard:

1. **Session picker** — lists existing sessions from the mucli backend, or
   choose "Create new session" and type a name
2. **Provider picker** — select Gemini, Ollama, or OpenAI
3. **Ollama local/cloud** — if Ollama selected, choose local daemon or
   Ollama Cloud (prompts for API key if cloud)
4. **Model picker** — fetches available models from the selected provider
   and presents them for selection

If you select an existing session that already has a provider and model
configured, the wizard **skips re-prompting** and loads directly.

```lua
-- No session configured — wizard launches on first use
require("mucli").setup({})
```

### Static Configuration

To skip the wizard, provide all required options in `setup()`:

```lua
require("mucli").setup({
  session = "my-session",
  provider = "openai",
  model = "gpt-4o",
})
```

### Required (when not using wizard)

| Option | Type | Description |
|--------|------|-------------|
| `session` | `string` | mucli session name. Must match an existing session or one will be created. |

### Optional

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `host` | `string` | `http://localhost:30311` | mucli GUI server URL |
| `provider` | `string\|nil` | `nil` | Provider name (e.g. `"openai"`, `"anthropic"`, `"ollama"`) |
| `model` | `string\|nil` | `nil` | Model name (e.g. `"gpt-4o"`, `"claude-sonnet-4-20250514"`) |
| `window.width` | `number` | `60` | Panel width in columns |
| `window.position` | `string` | `"right"` | Panel position: `"right"` or `"left"` |

### Keymaps

| Keymap | Default | Action |
|--------|---------|--------|
| `toggle_panel` | `<leader>mt` | Toggle chat panel open/closed |
| `send_visual` | `<leader>ms` | Send visual selection to agent as context |
| `send_file` | `<leader>mf` | Send current file to agent as context |
| `interrupt` | `<leader>mi` | Interrupt active agent turn |
| `accept_hunk` | `<leader>da` | Accept diff hunk in diff view |
| `reject_hunk` | `<leader>dr` | Reject diff hunk in diff view |

## Commands

| Command | Description |
|---------|-------------|
| `:Mucli` | Toggle chat panel |
| `:MucliSend` | Send visual selection (use with range, e.g. `:'<,'>MucliSend`) |
| `:MucliSendFile` | Send current file to agent |
| `:MucliInterrupt` | Interrupt active turn |
| `:MucliModel` | Switch model (with completion from available models) |
| `:MucliSession` | Show session status |
| `:MucliConfig` | Interactive configuration wizard (switch session, provider, model, or full setup) |
| `:MucliProvider [name]` | Switch provider (`gemini`, `ollama`, `openai`). Without arg, shows interactive picker. Ollama prompts for local/cloud. |

## Health Check

Run `:checkhealth mucli` to verify:
- plenary.nvim is loaded
- mucli server is reachable (`GET /healthz`)
- Session is configured
- Neovim extension is registered with mucli

## Architecture

```
extensions/neovim/
├── lua/mucli/
│   ├── init.lua          — setup() entry point, wires all modules
│   ├── config.lua        — defaults, deep-merge, validation
│   ├── client.lua        — plenary.curl HTTP client + SSE parser
│   ├── session.lua       — session lifecycle, model selection, extension registration
│   ├── context.lua       — visual selection + active file capture
│   ├── diff.lua          — unified diff parsing, side-by-side diff view, accept/reject
│   ├── tools.lua         — 5 neovim tool definitions + execution dispatch + SSE handler
│   ├── health.lua        — check_health() for :checkhealth
│   └── chat/
│       ├── panel.lua     — side-panel window management
│       ├── buffer.lua    — treesitter markdown, streaming, SSE event dispatcher
│       └── input.lua     — input line handling, CR to send, Ctrl-C to interrupt
├── plugin/
│   └── mucli.lua         — user commands (:Mucli, :MucliSend, etc.) + which-key
└── README.md
```

### Extension Registration

On `setup()`, the plugin calls `POST /api/extensions/register` with:

```json
{
  "extension_id": "neovim",
  "version": "1.0.0",
  "tool_prefix": "nvim_",
  "tools": [
    {"name": "nvim_open_file", "description": "...", "parameters": {...}},
    {"name": "nvim_jump_to_line", "description": "...", "parameters": {...}},
    {"name": "nvim_get_buffer_content", "description": "...", "parameters": {...}},
    {"name": "nvim_get_visual_selection", "description": "...", "parameters": {...}},
    {"name": "nvim_apply_diff", "description": "...", "parameters": {...}}
  ],
  "system_prompt": "NEOVIM EXTENSION TOOLS\n\nYou are connected to a Neovim editor..."
}
```

The mucli backend stores this in `session.extensions["neovim"]` and:
1. Appends the `system_prompt` to the agent's system prompt
2. Intercepts tool calls matching `nvim_*` prefix
3. Publishes `extension_tool_call` SSE events to the plugin
4. Waits for `POST /api/extensions/neovim/tool_result` from the plugin

This is a **generic extension registry** — other editor extensions (VS Code, JetBrains) can register using the same API with their own `extension_id`, `tool_prefix`, and `tools`.

### Neovim Tools

The agent can call these tools when the neovim extension is active:

| Tool | Parameters | Description |
|------|-----------|-------------|
| `nvim_open_file` | `file_path: str`, `line?: int` | Open file in editor, optionally jump to line |
| `nvim_jump_to_line` | `line: int`, `col?: int` | Move cursor to line in current buffer |
| `nvim_get_buffer_content` | `bufnr?: int` | Get all lines of a buffer (defaults to current) |
| `nvim_get_visual_selection` | — | Get user's current visual selection text |
| `nvim_apply_diff` | `file_path: str`, `diff_text: str` | Open diff view for accept/reject |

## Troubleshooting

### "setup() requires a session name"

Set `session` in your config:
```lua
require("mucli").setup({
  session = "my-session",
})
```

### "mucli server not reachable"

1. Start mucli GUI server: `mucli gui` or `mucli --gui`
2. Check the host matches: `curl http://localhost:30311/healthz`
3. If running on a different host/port, set `host` in config

### "plenary.nvim not found"

Add plenary as a dependency:
```lua
dependencies = { "nvim-lua/plenary.nvim" }
```

### Chat buffer has no syntax highlighting

Install nvim-treesitter and ensure the markdown parser is installed:
```vim
:TSInstall markdown
```

### Diff view doesn't open

The agent must produce a unified diff (with `@@` hunk markers). The plugin detects diffs in `artifact_created` SSE events and assistant responses containing diff code blocks.

### Extension not registered with mucli

Run `:checkhealth mucli` to diagnose. The plugin calls `POST /api/extensions/register` during `setup()`. If the server isn't running at setup time, the registration will fail silently — restart neovim after starting the mucli server.

## License

Same as mucli (MIT).
