# Mucli for Neovim

Workspace-aware chat and investigation, source navigation, and reviewed edits.
Requires Neovim 0.10+, Python 3.10+, ripgrep, and a configured provider. The
default model is Ollama `deepseek-v4.1-flash`.

## Install

Install Mucli into the Python environment Neovim uses:

```sh
cd /path/to/Mu-CLI
python3 -m pip install -e .
```

With lazy.nvim / AstroNvim:

```lua
{
  dir = '/path/to/Mu-CLI/integrations/nvim',
  name = 'mucli.nvim',
  main = 'mucli',
  lazy = false,
  opts = {
    command = { '/path/to/python3', '-m', 'mu.editor' },
    analysis = { provider = { name = 'ollama', model = 'deepseek-v4.1-flash' } },
  },
}
```

Alternatively append the plugin directory to `runtimepath` and call
`require('mucli').setup({...})`. Run `:checkhealth mucli`. Credentials come from
Neovim's environment: use `OLLAMA_API_KEY` for cloud or `OLLAMA_HOST` for another
server. No GUI server or Node is required; models are not
downloaded automatically.

## Workspace context

A background index maintains code chunks, declaration symbols, and import-path
lookup. Unchanged entries are reused. A scrubbed SQLite cache under
`$MUCLI_HOME/editor/index/` (default `~/.mucli/editor/index/`) speeds subsequent
starts. No embedding service is required.

Investigation combines nearby code with enclosing declarations, imports, ranked
workspace definitions and usage examples, unsaved buffers, recent edits, and
nearby diagnostics. Tree-sitter supplies scope ranges when available; lexical
scope extraction is the fallback. Cached LSP definition/type locations are read
from current buffer or disk content. Language-server requests never block the editor.

Buffers synchronize when changed; requests reference their versions.
Requests freeze those versions before entering the queue. Disk changes refresh
approximately every ten seconds; participating unsaved buffers override disk
immediately. Startup coverage grows while indexing runs, with bounded import
lookup during a cold start.

`:MucliContext` shows actual selected source ranges, enclosing declarations,
estimated tokens, retrieval time, and index coverage. `gd` opens an inspected
source. `:MucliStatus` reports the configured model and workspace.

Indexing considers up to 4,000 discovered paths and 24 MB of eligible disk files;
individual disk files are limited to 200 KB. Requests select up to six bounded
source ranges within an estimated total budget of at most 6,000 tokens, reduced
for smaller contexts. `provider.num_ctx` accepts 1,024–131,072. Symbol/import
matching is lexical with LSP enrichment, not a complete dependency graph for every
language. Bounded coverage is reported in the context view.

## Investigate and edit

| Command | Action |
| --- | --- |
| `:MucliExplain` | Explain cursor/selected code in the right-hand conversation |
| `:MucliExplain!` | Explain in a scrollable cursor popup; also kept in history |
| `:MucliAsk [question]` | Investigation with read/search tools and follow-up history |
| `:MucliChat` | Open/close the right-hand conversation panel |
| `:MucliFocus` | Focus the popup, or open/focus the conversation |
| `:MucliScrollDown` / `:MucliScrollUp` | Scroll the popup/panel without leaving code |
| `:MucliClear` | Start a fresh investigation |
| `:MucliSearch query` | Local bounded lexical search into quickfix; no model call |
| `:MucliReferences` | LSP references, with lexical search fallback |
| `:MucliSources` | Open inspected sources in quickfix |
| `:MucliEdit [request]` | Propose a selected-line/current-line replacement |
| `:MucliFix` | Propose a fix using diagnostics |
| `:MucliApply` / `:MucliReject` | Approve/reject the proposal |
| `:MucliCancel` | Cancel current work |

Ask/Edit open an input prompt when arguments are omitted. Explain, questions,
replies, and proposed edits share a right-hand conversation. New requests open it
without taking focus from your code. Replies preserve your reading position.
Context inspection opens a separate focused popup instead of replacing history.

Questions from the panel use its source file, or a recent eligible buffer in the
same workspace when no source is attached yet. Hidden files retain their last
cursor position. Ask/Edit keep the chosen file while the input prompt is open;
closing that file cancels submission with a short message. If no eligible file
is open, open a named source file before asking.

Explain is a quick answer: normally two to four short sentences about the selected
code or a small block at the cursor. It uses current unsaved code, referenced
definitions, and applicable imports/types. Python blocks and annotated receivers
are resolved from syntax; other languages use lexical/LSP context. It does not
search for the generic Explain instruction or inherit an earlier explanation's
subject. Ask a follow-up when you want callers, design details, or a deeper dive.
Ordinary questions also default to a brief answer, expanding when requested.

Transcript labels are compact. Automatic Explain instructions are hidden, and
source lists start collapsed: **s** expands them and **gs** opens quickfix. **?**
shows the navigation keys. References inside replies remain navigable with **gd**.
Investigations finish with an explicit answer step. An empty or tool-only reply
gets one automatic retry using the collected evidence; reasoning text is never
used as the answer. If that still fails, the request is visibly marked as failed.
Press **r** on a failed request to retry its question without retyping it. Failed
responses do not enter the model's follow-up history.

The installed AstroNvim mappings are:

| Key | Action |
| --- | --- |
| **Space mm** | Toggle the conversation panel |
| **Space mw** | Focus the popup or conversation |
| **Space mj / mk** | Scroll down/up while staying in code |
| **Space me** | Explain in the conversation |
| **Space mE** | Explain in a popup |
| **Space ma** | Ask / follow up |
| **Space mn** | Clear this workspace's investigation |

Inside the panel, **j/k** move by displayed line, **Ctrl-d/u** scroll half pages,
**Ctrl-f/b** scroll full pages, **gg/G** jump to the beginning/end, and **[q / ]q**
jump between requests. **Enter** asks a follow-up, **gd** opens an inspected source
from the reply under the cursor, and **gs** opens its sources in quickfix.
**Esc** returns to code while keeping the panel open; **q** closes the panel.
Focused popups support the same standard scrolling and **q/Esc** to close.

Each workspace retains up to 50 displayed requests, trimmed to about 256 KB of
question/response text, in the current Neovim session. Closing/reopening the panel
preserves it; restarting Neovim clears it. Histories are separate between
workspaces and independent of terminal Mucli sessions. The model's follow-up
context remains bounded to six exchanges and 24,000 characters, so older visible
history is not all resent on each request. `:MucliClear` clears both the visible
conversation and backend follow-up context. Explain has up to two read/search
rounds; deeper investigations have six, with four executed calls per round.
Repeated identical lookups within a request reuse their results.
Investigation search scans at most 12 MB per query,
separately from inline retrieval.

Edits use a focused floating replacement preview and highlight the affected source
range. **a** or **Space-a** approves; **q** rejects. Source/dependency changes
invalidate the proposal. Approval changes only buffer contents, remains undoable,
and never saves automatically. The service has no shell/write tools.

## Scope and exclusions

Root discovery uses `.git`, `.mucliignore`, `pyproject.toml`, `package.json`,
`Cargo.toml`, or `go.mod`. Set `workspace = '/absolute/project'` for unusual layouts.
Only buffers from that workspace are shared. Each root has a private stdio worker;
no network listener is opened.

Set `share_open_buffers = false` to stop synchronizing other buffers. Requests
send selected context to the configured provider only when you ask.

Disk discovery honors `.gitignore`/`.ignore`; explicitly open buffers can include
gitignored files. To exclude paths from **all** context, use `.mucliignore`
(exclusion-only globs/directory prefixes, no `!` negation):

```text
private/
*.secret.*
production-data/
```

Workspace bounds, secret-path checks, and secret scrubbing apply to retrieved
context and results. Ignore changes invalidate affected cached disk context.
Automatic retrieval skips vendor/generated bundles and lockfiles; explicit reads
remain available. Symbol search matches whole identifiers rather than substrings.
Unsaved buffers over 500 KB are excluded. Existing session L0–L5 packaging and
approval behavior are unaffected.

## Verification

`make test-nvim` covers real stdio RPC, workspace context, LSP results,
dependency invalidation, edit review, conversation history, scrolling,
historical source navigation, empty-response recovery, retrying a failed
request, asynchronous input, and source recovery from an empty split without
mixing workspaces. `python3 -m pytest tests/test_editor*.py` covers the Python
service: workspace bounds, secret scrubbing, read-only tools, and edit limits.
