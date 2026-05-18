"""`batch_job` (composite) and `flush` (collation drain) handlers.

`batch_job` runs a list of nested tool calls in sequence via the
canonical `dispatch(...)` entry point. Nested batches are refused and
return an `unsupported` envelope so the model can't dynamite the
dispatcher with a Russian-doll structure.

`flush` is a sentinel: the session loop intercepts it before this
handler ever runs (the collation buffer drain happens there). The
handler body here is a safety net that returns a clean
"Buffer flushed." message if the legacy path ever invokes it directly.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from mu.tools import tool

from .._envelope import _build_tool_envelope, _envelope_from_handler_result


@tool(
    name="batch_job",
    description=(
        "Executes multiple tool calls in sequence. Returns the results "
        "of all calls in the order they were provided."
    ),
    parameters={
        "type": "object",
        "properties": {
            "commands": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "The name of the tool to execute.",
                        },
                        "tool_args": {"type": "object"},
                    },
                    "required": ["tool_name", "tool_args"],
                },
            }
        },
        "required": ["commands"],
    },
    # The composite tool itself never modifies state directly — the
    # NESTED calls go through approval. The legacy comment was:
    # "We will query the individual tools and only require a single approval"
    requires_approval=False,
    execution_kind="composite",
    preview_policy="optional",
    result_mode="structured",
)
def batch_job(args: Dict[str, Any], context) -> str:
    from .._dispatcher import dispatch
    from core.tools import get_tool_descriptor

    commands = args.get("commands", [])
    if not isinstance(commands, list):
        return json.dumps(
            _build_tool_envelope(
                tool_name="batch_job",
                ok=False,
                error_code="invalid_args",
                message="Error: 'commands' must be a list.",
                data={"children": []},
            )
        )

    children = []
    for i, cmd in enumerate(commands):
        if not isinstance(cmd, dict):
            children.append(
                {
                    "index": i,
                    "tool_name": None,
                    "result": _build_tool_envelope(
                        tool_name="batch_job",
                        ok=False,
                        error_code="invalid_args",
                        message=f"Error: Command {i} - invalid command entry.",
                    ),
                }
            )
            continue

        name = cmd.get("tool_name")
        t_args = cmd.get("tool_args", {})

        if not name:
            children.append(
                {
                    "index": i,
                    "tool_name": None,
                    "result": _build_tool_envelope(
                        tool_name="batch_job",
                        ok=False,
                        error_code="invalid_args",
                        message=f"Error: Command {i} - tool_name missing.",
                    ),
                }
            )
            continue

        nested_descriptor = get_tool_descriptor(name)
        if not nested_descriptor:
            children.append(
                {
                    "index": i,
                    "tool_name": name,
                    "result": _build_tool_envelope(
                        tool_name=name,
                        ok=False,
                        error_code="not_found",
                        message=f"Error: Command {i} - unknown tool: {name}",
                    ),
                }
            )
            continue

        if nested_descriptor.execution_kind == "composite":
            children.append(
                {
                    "index": i,
                    "tool_name": name,
                    "result": _build_tool_envelope(
                        tool_name=name,
                        ok=False,
                        error_code="unsupported",
                        message=f"Error: Command {i} - nested batch_job not allowed.",
                    ),
                }
            )
            continue

        if context.ui:
            context.ui.show_info(
                f"  [{i + 1}/{len(commands)}] Executing in batch: {name}"
            )

        res = dispatch(
            name,
            t_args,
            context.folder_context,
            context.ui,
            context.variables,
            invocation_source=context.invocation_source,
            session=context.session,
        )
        try:
            child_result = json.loads(res)
        except Exception:
            child_result = _envelope_from_handler_result(name, res)
        children.append({"index": i, "tool_name": name, "result": child_result})

    ok = all(bool(item.get("result", {}).get("ok")) for item in children)
    failures = sum(1 for c in children if not c.get("result", {}).get("ok"))
    message = (
        f"Batch completed with {len(children)} command(s)."
        if ok
        else f"Batch completed with failures in {failures} command(s)."
    )
    return json.dumps(
        _build_tool_envelope(
            tool_name="batch_job",
            ok=ok,
            error_code=None if ok else "execution_failed",
            message=message,
            data={"children": children, "count": len(children)},
        ),
        indent=2,
    )


@tool(
    name="flush",
    description=(
        "Flushes the collation buffer and returns all the gathered "
        "context to the model. Use this when you have finished gathering "
        "all the necessary information and are ready to process it."
    ),
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="control",
    preview_policy="none",
    result_mode="raw",
    server_policy="session_only",
)
def flush(args: Dict[str, Any], context) -> str:
    """Sentinel handler. The session loop intercepts `flush` before
    dispatch runs (the actual collation-buffer drain happens there);
    this body is a safety net for any caller that invokes the
    dispatcher directly with `tool_name="flush"`."""
    return "Buffer flushed."
