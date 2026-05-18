# Hooks

mucli's agent loop fires hooks at five lifecycle points. Hooks observe
or intervene at points where the loop talks to the model or to a tool —
useful for auditing, denying risky calls, telemetry, and auto-compaction.

This document covers what's implemented, the two registration paths
(Python and shell), and the built-in hooks that ship with mucli.

## What is implemented

The registry lives at `mu/agent/hooks.py`. Shell-hook loading lives at
`mu/agent/hooks_config.py`. The loop fires hooks from
`core/session.py` (tool + provider points) and `mu/agent/loop.py`
(`on_stop`).

| Hook point | Fires when | `short_circuit` honored? | `abort` honored? |
| --- | --- | --- | --- |
| `pre_provider_call` | About to send messages to the LLM | ❌ | ✅ (raises `_HookAbort`, skips the provider call) |
| `post_provider_call` | Just got the model response | ❌ | ✅ (loop exits at next boundary) |
| `pre_tool` | About to execute a tool | ✅ | ✅ (tool is skipped with a `hook_aborted` envelope) |
| `post_tool` | Tool returned a result | ❌ | ✅ (loop exits at next boundary; current tool result survives) |
| `on_stop` | The loop is ending (success / max-iter / error) | ❌ | ❌ (loop is already terminating) |

| Capability | Status |
| --- | --- |
| Python-decorator registration | ✅ `@default_registry.register(point, priority=…)` |
| Shell-command registration via `.mu/hooks.json` | ✅ Loaded at startup |
| Priority ordering (lower runs first) | ✅ |
| Exception isolation (a buggy hook doesn't kill others) | ✅ |
| `short_circuit` to refuse a tool call | ✅ At `pre_tool` only |
| `abort` to ask the loop to stop | ✅ Acted on at every fire site except `on_stop` |
| `data` patch (dict return merged into results) | ✅ Returned; consumer code can read it |
| Async handlers | ❌ Hooks fire synchronously |
| Per-session registries | ❌ One module-level `default_registry` |
| Shell-hook timeout | ✅ Default 5s, configurable per entry |
| Shell-hook output capture | ✅ stdout truncated to 1 KB and surfaced in `data` |

## How it works

```
┌────────────────────────────────────────────────────────────────┐
│ Session.send_message(...) / AgentLoop.run_turn(...)             │
│                                                                  │
│   ─── pre_provider_call ───►   compactor (auto-rolls history)   │
│        Session._provider_generate_with_retry(...)                │
│   ─── post_provider_call ──►   (custom hooks)                   │
│                                                                  │
│   for each tool_call the model emitted:                          │
│       ─── pre_tool ─────►  plan_mode      (short-circuits writes)│
│                            secret_guard   (short-circuits leaks) │
│                            usage_tracker  (stamps start time)    │
│                            cfg:* (your `.mu/hooks.json` entries) │
│       if any returned short_circuit → use that payload as result │
│       else → execute the tool                                    │
│       ─── post_tool ────►  usage_tracker  (records elapsed_ms)   │
│                                                                  │
│   ─── on_stop ─────────►   (custom hooks)                       │
└────────────────────────────────────────────────────────────────┘
```

Hooks at the same point fire in priority order (lower first). Within
equal priority, registration order is preserved.

## Built-in hooks

These are imported by the package and auto-install on first import.
Remove them with `default_registry.remove("<name>")` if you need to.

| Name | Point | Purpose |
| --- | --- | --- |
| `plan_mode_block_writes` | `pre_tool` (pri 10) | Short-circuits write-side tools when `plan_mode` is on (`mu/agent/plan_mode.py`) |
| `bash_secret_guard` | `pre_tool` | Short-circuits `bash` / `bash_background` commands targeting denied paths (~/.ssh, /etc/shadow, ~/.aws, etc.) or matching risky patterns (`env`, `find / -name id_rsa`, …). Bypass with `/set security_allow_secret_paths true`. (`mu/agent/secret_guard.py`) |
| `usage_tracker_pre` / `usage_tracker_post` | `pre_tool` / `post_tool` | Per-tool counters, elapsed-time tracking, skill-invocation banner. Feeds `/stats`. (`mu/agent/usage_tracker.py`) |
| `auto_compactor` | `pre_provider_call` | Rolls history into the conversation summary when estimated tokens exceed `context_token_limit * context_trim_threshold` (default 0.85). (`mu/agent/compactor.py`) |

## Adding a Python hook

The decorator path is the idiomatic one for hooks shipped with the
codebase. Register at import time:

```python
from mu.agent.hooks import HookContext, HookResult, default_registry


@default_registry.register("pre_tool", priority=50)
def deny_writes_to_etc(ctx: HookContext):
    if ctx.tool_name not in ("write_file", "apply_diff"):
        return None
    target = (ctx.tool_args or {}).get("filename", "")
    if target.startswith("/etc/"):
        return HookResult(
            action="short_circuit",
            payload={
                "ok": False,
                "error_code": "hook_denied",
                "message": "Refusing to write under /etc/.",
                "data": {"tool_name": ctx.tool_name},
                "artifacts": [],
                "telemetry": {"tool_name": ctx.tool_name},
            },
        )
    return None
```

Return values:
- `None` — nothing happens; the loop continues.
- `HookResult(action="continue", data={...})` — same, but `data` is
  collected and returned to the caller of `registry.fire(...)`.
- `HookResult(action="short_circuit", payload=...)` — at `pre_tool`,
  the loop uses `payload` as the tool result instead of executing.
  Other hook points collect the result but don't act on it.
- `HookResult(action="abort", payload="<reason>")` — request the loop
  to stop. Honored at every fire site except `on_stop`; see [Stopping
  the loop](#stopping-the-loop-with-actionabort) below for per-point
  semantics.
- A bare `dict` — wrapped as `HookResult(action="continue", data=dict)`.

`HookContext` fields populated at each point:

| Field | Populated at |
| --- | --- |
| `point` | always |
| `session`, `variables` | always |
| `tool_name`, `tool_args` | `pre_tool`, `post_tool` |
| `tool_result` | `post_tool` |
| `messages`, `system_prompt`, `tools` | `pre_provider_call` |
| `response` | `post_provider_call` |
| `stop_reason` | `on_stop` |
| `metadata` | shared scratch dict across `pre_*` / `post_*` of the same call |

## Adding a shell hook (`.mu/hooks.json`)

For project-local or workspace-local rules that should not require a
code change. The file lives at `.mu/hooks.json` relative to the
working directory and is loaded on startup.

```json
{
  "hooks": [
    {
      "name": "log-tool-calls",
      "point": "post_tool",
      "priority": 200,
      "command": "echo \"$MU_TOOL_NAME $MU_TOOL_ARGS_JSON\" >> /tmp/mucli-tools.log"
    },
    {
      "name": "deny-rm-rf",
      "point": "pre_tool",
      "priority": 5,
      "command": "case \"$MU_TOOL_ARGS_JSON\" in *rm\\ -rf*) exit 1;; *) exit 0;; esac",
      "on_failure": "short_circuit",
      "message": "rm -rf detected; refuse"
    }
  ]
}
```

Each entry runs the given shell command and consults the exit code:
- `0` → continue.
- non-zero with `on_failure: "short_circuit"` at `pre_tool` → refuse the
  call with `message` as the user-facing reason. The model receives a
  structured envelope with `error_code: "hook_denied"`.
- non-zero otherwise → logged at info-level; the loop continues.

Fields:

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Used in logs and for de-duplication on reload. Stored as `cfg:<name>` in the registry. |
| `point` | yes | One of `pre_provider_call`, `post_provider_call`, `pre_tool`, `post_tool`, `on_stop`. |
| `command` | yes | Run with `sh -c`. |
| `priority` | no | Default 100. Lower runs first. |
| `on_failure` | no | `"log"` (default) or `"short_circuit"`. `short_circuit` is only honored at `pre_tool`. |
| `message` | no | Refusal message when short-circuiting. |
| `timeout_seconds` | no | Default 5.0. Timed-out commands are treated as failures. |

Environment variables passed to every command:

| Var | When | Contents |
| --- | --- | --- |
| `MU_HOOK_POINT` | always | The hook point name |
| `MU_TOOL_NAME` | `pre_tool`, `post_tool` | The tool being called |
| `MU_TOOL_ARGS_JSON` | `pre_tool`, `post_tool` | JSON-encoded `tool_args` |
| `MU_SYSTEM_PROMPT` | `pre_provider_call` | First 4 KB of the system prompt |
| `MU_STOP_REASON` | `on_stop` | Reason the loop is ending |

The parent process environment is inherited, so secrets / paths already
in your shell are available to the hook.

## Inspecting registered hooks

There is no slash command for this yet. From a Python REPL or a script:

```python
from mu.agent.hooks import default_registry

for spec in default_registry.list():
    print(f"{spec.point:22} pri={spec.priority:<4} {spec.name}")
```

Shell-loaded hooks appear with the `cfg:` prefix on their name.

## Stopping the loop with `action="abort"`

A hook can stop the agentic loop by returning `HookResult(action="abort",
payload="<reason>")` at any of `pre_provider_call`,
`post_provider_call`, `pre_tool`, or `post_tool`. The fire site sets
`session._hook_abort_requested = True` and stores the payload in
`session._hook_abort_reason`. The loop honors it at the **next
iteration boundary**, exiting the turn with status `"hook_aborted"` and
the reason in the turn-response `error` field.

Per-point semantics:

- **`pre_provider_call` abort** — the provider call is skipped
  entirely. A `_HookAbort` exception unwinds the retry wrapper (so the
  retry logic doesn't treat it as a transient failure) and is caught by
  the iteration loop, which returns the `hook_aborted` turn response.
  No model tokens are spent.
- **`post_provider_call` abort** — the response is kept and stored in
  history. If the response had no tool calls, the loop exits with
  `hook_aborted` after that iteration. If it had tool calls, they run
  to completion, then the loop exits.
- **`pre_tool` abort** — the tool is skipped. A synthetic
  `error_code: "hook_aborted"` envelope is returned to the dispatcher
  in place of the real tool result so the model sees a clear refusal.
  The loop exits after the current dispatch batch.
- **`post_tool` abort** — the tool's real result survives (it already
  ran). The loop exits after the current dispatch batch.
- **`on_stop` abort** — collected but ignored; the loop is already
  terminating.

First-wins: if multiple hooks abort in the same turn, the **first**
reason is preserved on the session so the user sees the actual cause,
not the last one to fire.

Use `abort` for terminal conditions (budget exhausted, policy
violation, user kill switch). Use `short_circuit` at `pre_tool` if you
just want to refuse one tool call and let the loop keep running.

## Limitations

- One process-wide `default_registry`. Tests construct their own
  `HookRegistry()` instance to avoid bleed-over.
- Abort is advisory at the iteration boundary — it does not cancel an
  in-flight provider stream or an in-flight tool execution. Once those
  return, the loop exits. There is no async cancellation.
- Hooks fire synchronously from the loop thread. Long-running shell
  hooks slow every iteration; consider `bash_background` instead.
- No retry — a hook that fails once fails for that turn.
- Shell hooks see truncated context (4 KB for `MU_SYSTEM_PROMPT`); for
  full access, register a Python hook.
