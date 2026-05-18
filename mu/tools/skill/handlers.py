"""`invoke_skill` `@tool` handler.

Loads the full body of an installed skill into the model's context.
Skill-invocation handler — body
unchanged, just rewrapped to read the workspace folders from the new
context shape.
"""

from typing import Any, Dict

from mu.tools import tool


@tool(
    name="invoke_skill",
    description=(
        "Load the full body of an installed skill into context. Use after "
        "seeing a skill name in the AVAILABLE SKILLS index that fits the "
        "user's request but wasn't auto-expanded. Call once per skill needed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact skill name from the AVAILABLE SKILLS index.",
            }
        },
        "required": ["name"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="raw",
    server_policy="session_only",
)
def invoke_skill(args: Dict[str, Any], context) -> str:
    from mu.skills import get_skill, render_skills_expanded

    name = str(args.get("name") or "").strip()
    if not name:
        return "Error: invoke_skill requires a non-empty `name` argument."
    folder_context = context.folder_context
    folders = list(getattr(folder_context, "folders", []) or [])
    skill = get_skill(name, folders)
    if skill is None:
        return f"Error: no skill named {name!r} is installed."
    return render_skills_expanded(skill)
