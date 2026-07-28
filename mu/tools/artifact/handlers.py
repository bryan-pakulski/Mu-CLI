"""Tools for publishing files or text as downloadable session artifacts."""
from __future__ import annotations

import json
import os
from typing import Any

from mu.artifact import ArtifactError
from mu.security.secret_paths import is_denied_path
from mu.tools import tool
from mu.tools.capabilities import normalize_session_type



def _validated_source_path(args: dict[str, Any], context) -> str | None:
    raw = args.get("file_path")
    if not raw:
        return None
    session = getattr(context, "session", None)
    variables = getattr(session, "variables", None) or getattr(context, "variables", None) or {}
    session_type = normalize_session_type(variables.get("session_type", "workspace"))
    if session_type == "chat":
        raise ArtifactError("chat sessions may publish inline content only")

    source = os.path.realpath(os.path.abspath(os.path.expanduser(str(raw))))
    denied, reason = is_denied_path(source, variables)
    if denied:
        raise ArtifactError(f"artifact source refused: {reason or 'secret path'}")

    if session_type == "workspace":
        folder_context = getattr(context, "folder_context", None) or getattr(session, "folder_context", None)
        folders = [
            os.path.realpath(os.path.abspath(os.path.expanduser(str(folder))))
            for folder in (getattr(folder_context, "folders", None) or [])
        ]
        if not folders:
            raise ArtifactError("workspace sessions require an attached folder for file artifacts")
        contained = False
        for root in folders:
            try:
                contained = os.path.commonpath([source, root]) == root
            except ValueError:
                contained = False
            if contained:
                break
        if not contained:
            raise ArtifactError("artifact source must be inside an attached workspace")
    return source


def _registry(context):
    session = getattr(context, "session", None)
    manager = getattr(session, "session_manager", None)
    registry = getattr(session, "artifact_registry", None) or getattr(
        manager, "artifact_registry", None
    )
    if registry is None:
        raise ArtifactError("artifact registry is unavailable for this session")
    return registry


@tool(
    name="upload_artifact",
    description=(
        "Publish a file or inline text as a downloadable session artifact. "
        "Provide exactly one of file_path or content. Container sessions use "
        "this for deliverables that must be returned to the user."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Download file name."},
            "content": {"type": "string", "description": "Inline UTF-8 content."},
            "file_path": {
                "type": "string",
                "description": "Existing file path in the current runtime.",
            },
            "mime_type": {"type": "string", "default": "application/octet-stream"},
        },
        "required": ["name"],
    },
    requires_approval=False,
    execution_kind="mutate",
    preview_policy="none",
    group="artifact",
)
def upload_artifact_tool(args: dict[str, Any], context) -> str:
    try:
        descriptor = _registry(context).add(
            name=str(args.get("name") or ""),
            source_path=_validated_source_path(args, context),
            content=(args.get("content") if "content" in args else None),
            mime_type=str(args.get("mime_type") or "application/octet-stream"),
        )
        session = getattr(context, "session", None)
        ui = getattr(context, "ui", None) or getattr(session, "ui", None)
        event = {"kind": "artifact_created", "artifact": descriptor}
        try:
            if ui is not None and hasattr(ui, "publish"):
                ui.publish(event)
            elif ui is not None and hasattr(ui, "_publish"):
                ui._publish(event)
        except Exception:
            # Registry persistence is authoritative; a missed live event is
            # recovered when clients refetch the artifact list.
            pass
        return json.dumps(
            {
                "ok": True,
                "message": f"Artifact ready: {descriptor['name']}",
                "artifact": descriptor,
                "artifacts": [descriptor],
            },
            indent=2,
        )
    except (ArtifactError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc), "artifacts": []}, indent=2)


@tool(
    name="list_artifacts",
    description="List downloadable artifacts already published for this session.",
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    group="artifact",
)
def list_artifacts_tool(_args: dict[str, Any], context) -> str:
    try:
        artifacts = _registry(context).list()
        return json.dumps({"ok": True, "artifacts": artifacts}, indent=2)
    except (ArtifactError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc), "artifacts": []}, indent=2)
