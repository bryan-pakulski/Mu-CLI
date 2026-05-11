"""Load hooks from a JSON config file and register them on a HookRegistry.

Config format (`.mu/hooks.json` in the working directory):

    {
      "hooks": [
        {
          "name": "log-tool-calls",
          "point": "post_tool",
          "priority": 200,
          "command": "echo $TOOL_NAME >> /tmp/tools.log"
        },
        {
          "name": "deny-rm-rf",
          "point": "pre_tool",
          "priority": 5,
          "command": "test ! '$TOOL_ARGS' == '*rm -rf*'",
          "on_failure": "short_circuit",
          "message": "rm -rf detected by deny-rm-rf hook"
        }
      ]
    }

Each entry runs a shell command and consults its exit code:
  * exit 0       → continue
  * non-zero AND `on_failure="short_circuit"` (only meaningful at
                    pre_tool) → short-circuit with `message` as the
                    refusal payload
  * non-zero otherwise → log and continue

The command receives the following environment variables:
  * MU_HOOK_POINT     - the hook point name
  * MU_TOOL_NAME      - present at pre/post_tool
  * MU_TOOL_ARGS_JSON - JSON-encoded args; present at pre/post_tool
  * MU_SYSTEM_PROMPT  - present at pre_provider_call (first 4 KB)
  * MU_STOP_REASON    - present at on_stop

This is intentionally narrow: only shell hooks. Python hooks register
via `default_registry.register(...)` directly.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from typing import Any, Dict, Optional

from .hooks import (
    HOOK_POINTS,
    HookContext,
    HookRegistry,
    HookResult,
    HookSpec,
    default_registry,
)


logger = logging.getLogger("mucli")


DEFAULT_CONFIG_PATH = os.path.join(".mu", "hooks.json")


def _build_env(ctx: HookContext) -> Dict[str, str]:
    env = os.environ.copy()
    env["MU_HOOK_POINT"] = ctx.point
    if ctx.tool_name:
        env["MU_TOOL_NAME"] = ctx.tool_name
    if ctx.tool_args is not None:
        try:
            env["MU_TOOL_ARGS_JSON"] = json.dumps(ctx.tool_args, default=str)
        except Exception:
            env["MU_TOOL_ARGS_JSON"] = str(ctx.tool_args)
    if ctx.system_prompt:
        # Cap to 4 KB so we don't blow out the env table.
        env["MU_SYSTEM_PROMPT"] = ctx.system_prompt[:4096]
    if ctx.stop_reason:
        env["MU_STOP_REASON"] = ctx.stop_reason
    return env


def _build_handler(spec_entry: Dict[str, Any]):
    command = str(spec_entry.get("command", "") or "").strip()
    on_failure = str(spec_entry.get("on_failure", "log") or "log").strip().lower()
    message = str(spec_entry.get("message", "") or "")
    timeout = float(spec_entry.get("timeout_seconds", 5.0) or 5.0)

    if not command:
        raise ValueError(
            f"hooks.json entry {spec_entry.get('name')!r} missing 'command'"
        )

    def handler(ctx: HookContext) -> Optional[HookResult]:
        env = _build_env(ctx)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "hooks.json: %s timed out after %.1fs; treating as failure",
                spec_entry.get("name"),
                timeout,
            )
            proc_returncode = -1
            stdout = ""
            stderr = "timeout"
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "hooks.json: %s raised %s; skipping", spec_entry.get("name"), exc
            )
            return None
        else:
            proc_returncode = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

        if proc_returncode == 0:
            return HookResult(
                action="continue",
                data={"stdout": stdout[:1024]},
            )

        if on_failure == "short_circuit" and ctx.point == "pre_tool":
            payload = {
                "ok": False,
                "error_code": "hook_denied",
                "message": message or stderr or f"Hook {spec_entry.get('name')} denied this call.",
                "data": {
                    "hook_name": spec_entry.get("name"),
                    "tool_name": ctx.tool_name,
                },
                "artifacts": [],
                "telemetry": {"tool_name": ctx.tool_name or ""},
            }
            return HookResult(
                action="short_circuit",
                payload=payload,
                data={"hook_name": spec_entry.get("name")},
            )

        logger.info(
            "hooks.json: %s returned %d at %s (stderr=%s)",
            spec_entry.get("name"),
            proc_returncode,
            ctx.point,
            stderr[:200],
        )
        return None

    return handler


def load_hooks_from_config(
    config_path: str = DEFAULT_CONFIG_PATH,
    *,
    registry: Optional[HookRegistry] = None,
    clear_previous: bool = True,
) -> int:
    """Read `config_path` and register each hook on `registry`.

    Returns the number of hooks registered. If the file is missing,
    returns 0 silently. If `clear_previous` is True, removes any hooks
    previously registered with names prefixed by `cfg:` to avoid
    duplicates on reload.
    """
    reg = registry or default_registry
    if not os.path.exists(config_path):
        return 0

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("hooks.json: failed to read %s: %s", config_path, exc)
        return 0

    entries = data.get("hooks", []) if isinstance(data, dict) else []
    if not isinstance(entries, list):
        logger.warning("hooks.json: top-level 'hooks' must be a list")
        return 0

    if clear_previous:
        # Remove anything we previously registered. We tag config-sourced
        # hooks with the prefix "cfg:" so they don't collide with Python
        # registrations.
        for spec in list(reg.list()):
            if spec.name.startswith("cfg:"):
                reg.remove(spec.name)

    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        point = entry.get("point")
        if point not in HOOK_POINTS:
            logger.warning(
                "hooks.json: skipping entry with unknown point %r", point
            )
            continue
        name = entry.get("name") or f"unnamed-{count}"
        priority = int(entry.get("priority", 100))
        try:
            handler = _build_handler(entry)
        except ValueError as exc:
            logger.warning("hooks.json: %s", exc)
            continue
        reg.add(
            HookSpec(
                name=f"cfg:{name}",
                point=point,
                priority=priority,
                handler=handler,
            )
        )
        count += 1

    if count:
        logger.info(
            "hooks.json: loaded %d hook(s) from %s", count, config_path
        )
    return count


__all__ = ["DEFAULT_CONFIG_PATH", "load_hooks_from_config"]
