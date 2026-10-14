"""In-sandbox ``run_in_environment`` tool (registered only inside a job stage with several
build environments). The host-side broker runs the command in a sibling container."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

TOOL_NAME = "run_in_environment"
MAX_RESULT_CHARS = 30_000


def description(environments: List[Dict[str, Any]]) -> str:
    lines = []
    for env in environments:
        where = ", ".join(env.get("paths") or []) or "the whole repository"
        extras = (" (Docker available)" if env.get("docker") else "") + (f" (GPUs {env['gpus']})" if env.get("gpus") else "")
        lines.append(f"- {env['name']}: {where}{extras}")
    return ("Run a shell command in another build environment of this job: a sibling container with that "
            "environment's toolchain, the same /workspace (your edits are visible) and the same isolation. "
            "Use it to build or test components whose toolchain your own container lacks. "
            "`cwd` is repository-relative. Environments:\n" + "\n".join(lines))


def register(environments: List[Dict[str, Any]]) -> None:
    from mu.tools import tool
    names = [e["name"] for e in environments]

    @tool(name=TOOL_NAME, description=description(environments), requires_approval=True, execution_kind="mutate",
          group="shell", parameters={
              "type": "object",
              "properties": {
                  "env": {"type": "string", "enum": names, "description": "Build environment to run in."},
                  "command": {"type": "string", "description": "Shell command, e.g. `npm test` or `cargo build`."},
                  "cwd": {"type": "string", "description": "Repository-relative working directory (default: repository root)."},
                  "timeout_s": {"type": "integer", "description": "Optional time limit in seconds."},
              },
              "required": ["env", "command"],
          })
    def run_in_environment(args, context):
        return run(dict(args or {}))


def run(args: Dict[str, Any]) -> str:
    import httpx
    from mu.security.secret_paths import redact_secrets
    transport = httpx.HTTPTransport(uds=os.environ["MUCLI_MODEL_RELAY"])
    output: List[str] = []
    size = 0
    result: Dict[str, Any] = {}
    with httpx.Client(transport=transport, timeout=None, trust_env=False) as client:
        with client.stream("POST", "http://relay/environment",
                           headers={"X-Mucli-Stage-Token": os.environ["MUCLI_STAGE_TOKEN"]},
                           json={k: args.get(k) for k in ("env", "command", "cwd", "timeout_s")}) as response:
            if response.status_code != 200:
                return f"run_in_environment failed: {response.read().decode('utf-8', 'replace')[:2000]}"
            for line in response.iter_lines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") == "output":
                    text = str(event.get("text") or "")
                    size += len(text)
                    output.append(text)
                    if size > MAX_RESULT_CHARS * 2:
                        joined = "".join(output)[-MAX_RESULT_CHARS:]
                        output, size = [joined], len(joined)
                elif event.get("event") == "result":
                    result = event
    if result.get("error"):
        return f"run_in_environment failed: {result['error']}"
    text = "".join(output) or (str(result.get("stdout") or "") + str(result.get("stderr") or ""))
    text = redact_secrets(text)[0]
    if len(text) > MAX_RESULT_CHARS:
        text = "[… output truncated …]\n" + text[-MAX_RESULT_CHARS:]
    status = "timed out" if result.get("timed_out") else f"exit code {result.get('return_code')}"
    header = f"[{result.get('env') or args.get('env')}] {args.get('command')} → {status} ({result.get('duration_ms', 0)} ms)"
    if result.get("missing_executable"):
        header += f"\n{result['missing_executable']} is not installed in build environment {result.get('env')}"
    return header + "\n" + text
