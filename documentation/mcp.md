# MCP — Model Context Protocol

mucli speaks [MCP](https://modelcontextprotocol.io) to discover tools from
external processes — a filesystem server, a git server, a knowledge-graph
server, anything that implements the protocol. Once registered, MCP
tools are indistinguishable from native tools: they appear in `/tool
list`, go through the same approval/preview/collation pipeline, and the
model calls them the same way.

This document covers mucli's MCP implementation, its setup, and its
limits.

## What is implemented

mucli's MCP client lives at `mu/mcp/client.py` and the discovery /
registration layer at `mu/mcp/registry.py`. Runtime management is the
`/mcp` slash command.

| Feature | Status |
| --- | --- |
| **stdio transport** | ✅ Full. JSON-RPC 2.0, one message per line. |
| `initialize` handshake | ✅ Protocol version `2024-11-05`. |
| `notifications/initialized` | ✅ Sent after handshake. |
| `tools/list` | ✅ Tools discovered at server startup. |
| `tools/call` | ✅ With structured-content unwrap and `isError` handling. |
| Process lifecycle | ✅ Spawn, graceful terminate (2s) → kill, stderr capture. |
| Tool namespacing | ✅ `mcp__<server>__<tool>` — no cross-server collisions. |
| Per-server env vars | ✅ Merged with parent env, passed to subprocess. |
| Per-server working dir | ✅ `cwd` field. |
| Runtime management | ✅ `/mcp list \| status \| reload \| debug <server>`. |
| Close on exit | ✅ Clients are closed when the REPL terminates. |
| `resources/list`, `resources/read` | ❌ Not implemented. |
| `prompts/list`, `prompts/get` | ❌ Not implemented. |
| `logging/setLevel` | ❌ Not implemented. |
| `sampling/createMessage` (server → client) | ❌ Not implemented. |
| Progress notifications, cancellation | ❌ Not implemented. |
| HTTP / SSE / WebSocket transport | ❌ Not implemented (stdio only). |
| Concurrent requests per server | ❌ Single-threaded blocking client. |
| Read timeout enforcement | ⚠️ Field defined; not enforced. A hung server will block. |
| Automatic restart on crash | ❌ Use `/mcp reload`. |

The implementation is **production-ready for stdio-based tool servers**
that don't depend on resources / prompts / sampling. If your server is
HTTP-only or relies on those subsystems, this client won't drive it.

## How it works

```
┌──────────────────────────────────────────────────────────────────────┐
│ Startup                                                               │
│   1. mucli reads .mu/mcp.json                                        │
│   2. For each server entry: spawn subprocess (Popen, stdin/stdout)   │
│   3. Send `initialize` request → wait for response                   │
│   4. Send `notifications/initialized`                                │
│   5. Request `tools/list` → parse MCPTool[]                          │
│   6. Register each tool as `mcp__<server>__<tool>` in mu.tools       │
│   7. Tool also appended to legacy mu.tools.descriptors.TOOLS for system prompt │
└──────────────────────────────────────────────────────────────────────┘
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Model invokes mcp__fs__read_file({"path": "..."})                    │
│   1. Dispatch into the registered handler                            │
│   2. Handler calls client.call_tool("read_file", {...})              │
│   3. Client sends JSON-RPC request, blocks reading the response      │
│   4. Response `content` blocks of type "text" flattened into message │
│   5. Full structured content kept in `data.content`                  │
│   6. Result wrapped in mucli's standard tool envelope                │
└──────────────────────────────────────────────────────────────────────┘
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Shutdown (REPL exits, /quit, or process termination)                 │
│   • close_all() called on every open client                          │
│   • terminate() → wait(2s) → kill() if still alive                   │
└──────────────────────────────────────────────────────────────────────┘
```

Tool naming is server-scoped: `mcp__<server>__<tool>`. Two servers can
each expose a `read` tool and they appear as `mcp__a__read` and
`mcp__b__read` with no collision.

## Setup

### Configuration file

Drop a `.mu/mcp.json` in the directory you launch mucli from:

```json
{
  "servers": {
    "fs": {
      "command": ["npx", "@modelcontextprotocol/server-filesystem", "/workspace"]
    },
    "memory": {
      "command": ["mcp-server-memory"],
      "env": {"MEMORY_FILE": "/tmp/memory.json"}
    },
    "git": {
      "command": ["mcp-server-git"],
      "env": {"GIT_REPO": "/workspace"},
      "cwd": "/workspace"
    }
  }
}
```

### Per-server schema

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `command` | array of strings | yes | Executable + args. First element is the binary. |
| `env` | object | no | Environment variables. Merged with the parent process env (per-server overrides take precedence). |
| `cwd` | string | no | Working directory for the spawned process. |

Unknown fields are ignored. Malformed entries log a warning and the
server is skipped — one bad server never breaks the rest.

### Validation behavior

- Missing `.mu/mcp.json` → no MCP, no warning.
- Invalid JSON → warning, no servers registered.
- Server entry not an object → warning, that entry skipped.
- Entry missing `command` → warning, that entry skipped.
- Server fails to start, fails the handshake, or fails `tools/list` →
  warning with stderr (capped at 500 chars), other servers continue.
- Process spawn fails → warning with the OS error.

### Verifying setup

After launch:

```
/mcp list      → shows configured servers and their status
/mcp status    → tool counts per server, capabilities advertised
/tool list     → all tools, native + mcp__<server>__<tool> entries
```

If a server is listed in `/mcp list` as `dead`, run `/mcp debug <name>`
for its stderr.

## Authentication

mucli has **no built-in OAuth, JWT, or bearer-token flow**. All
authentication is delegated to the MCP server itself, configured at
process spawn time:

1. **Environment variables** (most common). Put the secret in `env`:

   ```json
   {
     "servers": {
       "github": {
         "command": ["mcp-server-github"],
         "env": {"GITHUB_TOKEN": "ghp_xxxxxxxxxxxxxxxx"}
       }
     }
   }
   ```

   The token is passed via the process environment, never logged.
   Anything the server does with it is up to that server.

2. **Command-line arguments**:

   ```json
   {
     "servers": {
       "api": {
         "command": ["mcp-server-api", "--api-key", "sk-xxxxxxxx"]
       }
     }
   }
   ```

3. **External credential files** referenced by env var or arg:

   ```json
   {
     "servers": {
       "aws": {
         "command": ["mcp-server-aws"],
         "env": {"AWS_PROFILE": "dev", "AWS_CONFIG_FILE": "/home/me/.aws/config"}
       }
     }
   }
   ```

### Avoiding secrets in `mcp.json`

Don't commit `mcp.json` with a real token. Either:

- Reference env vars from your shell using a launcher script, e.g.
  `command: ["sh", "-c", "GITHUB_TOKEN=$GITHUB_TOKEN mcp-server-github"]`.
- Use a credential helper / vault as the binary itself.
- `.gitignore` the `.mu/mcp.json` and check in `.mu/mcp.json.example`.

mucli does not redact environment values from logs (stderr is captured
verbatim). If your MCP server prints its credentials on startup, fix
the server.

## CLI

| Command | Description |
| --- | --- |
| `/mcp` or `/mcp list` | List configured servers with status (ok / dead / not started). |
| `/mcp status` | Per-server tool count + capabilities reported in `initialize`. |
| `/mcp reload` | Tear down every open server, re-read `.mu/mcp.json`, restart them all. |
| `/mcp debug <server>` | Show recent stderr / last-error for one server. |
| `/tool list` | All tools (native + MCP) with on/off state. MCP tools have `[MCP:<server>]` prefix in the description. |
| `/tool disable mcp__<server>__<tool>` | Hide an MCP tool from the model without restarting the server. |

The `/mcp` subcommands are tab-completable.

## Invoking MCP tools from the model

Nothing special is required. After registration:

```
read_file({"path": "..."})        # native tool
mcp__fs__read_file({"path": "..."}) # MCP tool — same call shape
```

The handler:

1. Calls `tools/call` on the originating server.
2. If the server returns `isError: true`, the envelope's `ok` is `false`
   and `error_code` is `"mcp_tool_error"`.
3. Content blocks of `type: "text"` are joined into the `message`
   field. The full content array (including non-text blocks like
   images or resources) is preserved in `data.content`.
4. `telemetry.mcp_server` records which server served the call.

### Approval / plan-mode

Every MCP tool is registered with `requires_approval=True`. In plan
mode they're treated as write-side tools (blocked). Disable individual
ones with `/tool disable mcp__<server>__<tool>` if you need plan mode
to allow specific reads.

## Logging and troubleshooting

mucli uses the `"mucli"` logger; MCP messages share that name.

| Symptom | Where to look | Likely cause |
| --- | --- | --- |
| Server in `/mcp list` shows `dead` | `/mcp debug <name>` shows its stderr | Bad command path, missing dep, crashed on startup |
| Tools missing from `/tool list` | `/mcp status` shows 0 tools for that server | Server's `tools/list` returned empty, or registration was skipped — check `~/.mucli/logs/` |
| `MCP error -32601: Method not found` | Tool name typo | Server doesn't expose that tool name |
| Tool call hangs the REPL | `/mcp debug <name>` may help; otherwise SIGINT | Hung server; read timeout is not enforced (known limit). Use `/mcp reload` to recover. |
| Server prints credentials on startup | Server's own logging | Fix in the server — mucli captures stderr verbatim |
| `MCP server '...' closed unexpectedly. stderr=...` | Inline in the result | Server crashed mid-call. `/mcp reload` to restart everything. |

Run mucli with `--debug` for verbose logging including the
notifications mucli ignores (they're logged at DEBUG level).

## Common server configurations

### Filesystem (npm)

```json
{
  "servers": {
    "fs": {
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
    }
  }
}
```

Exposes `read_file`, `write_file`, `list_directory`, etc. The single
positional arg restricts the server to that root.

### Git

```json
{
  "servers": {
    "git": {
      "command": ["mcp-server-git", "--repository", "/workspace"]
    }
  }
}
```

Exposes `git_log`, `git_diff`, `git_show`, etc.

### Custom Python server

```json
{
  "servers": {
    "myserver": {
      "command": ["python3", "/path/to/my_mcp_server.py"],
      "env": {"PYTHONUNBUFFERED": "1"}
    }
  }
}
```

`PYTHONUNBUFFERED=1` keeps line-buffered stdout reliable; without it
some Python servers buffer their JSON-RPC replies and look hung.

### Mocked server for testing

`tests/test_mu_mcp.py` ships a small inline server that's useful as a
copy-paste starting point. It handles `initialize`,
`notifications/initialized`, `tools/list`, `tools/call`, and returns
`-32601` for anything else.

## Gaps (what is NOT implemented)

Be explicit so you can plan around it:

- **No HTTP/SSE transport.** Only stdio. Servers that expose only an
  HTTP+SSE endpoint won't work without a stdio shim.
- **No resources subsystem.** `resources/list` and `resources/read`
  are not called; if your server exposes data only via resources
  (not tools), mucli won't see it.
- **No prompts subsystem.** `prompts/list` and `prompts/get` aren't
  called; server-defined prompt templates aren't surfaced.
- **No sampling.** Servers cannot request the model do completions on
  their behalf via `sampling/createMessage`.
- **No progress / cancellation.** Long-running calls block until they
  return or the server is killed.
- **No read timeout.** A wedged server will hang the call. Workaround:
  `/mcp reload` from another terminal or restart mucli.
- **No automatic restart.** A crashed server stays dead until you
  `/mcp reload`.
- **No concurrent in-flight requests per server.** Calls are
  serialized per client. Multiple servers run in parallel because each
  is its own process.
- **No schema validation of tool arguments.** Whatever schema the
  server advertises is passed through to the model; arguments aren't
  validated locally before the call.

These are not bugs — they're scope choices. The implementation is
sufficient for stdio-based tool servers, which is the common case.

## Architecture notes

`mu/mcp/client.py:MCPClient` is a thin JSON-RPC-2.0 client over the
subprocess pipes. `_send` and `_recv` are deliberately small so an HTTP
or SSE transport could be added by swapping them out — the request/id
plumbing is transport-agnostic. If you write that, you'll also want a
request-id demultiplexer so concurrent calls work; the current
single-flight assumption simplifies the client at the cost of
parallelism.

`mu/mcp/registry.py` is the only consumer of `MCPClient`; it owns the
config-parse-then-fan-out logic and the namespaced registration into
`mu.tools`. Adding `resources` or `prompts` support would extend
`MCPClient` with the new request methods and then surface them either
as additional tools (e.g. `mcp__<server>__resource__<id>`) or as a
separate context-injection layer in the system prompt.

## Where to read further

- `mu/mcp/client.py` — the JSON-RPC stdio client.
- `mu/mcp/registry.py` — discovery, batch open, tool registration.
- `mu/commands/mcp.py` — the `/mcp` slash command.
- `tests/test_mu_mcp.py` — round-trip tests using an inline fake
  server (good copy-paste reference for writing your own server).
- `documentation/configuration.md` — `.mu/mcp.json` listed alongside
  the other config files.
- Upstream spec: <https://modelcontextprotocol.io/specification>
