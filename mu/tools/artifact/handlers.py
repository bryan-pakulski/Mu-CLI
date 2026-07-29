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
        session = getattr(context, "session", None)
        variables = (
            getattr(session, "variables", None)
            or getattr(context, "variables", None)
            or {}
        )
        session_type = normalize_session_type(
            variables.get("session_type", "workspace")
        )
        source_path = _validated_source_path(args, context)
        content = args.get("content") if "content" in args else None
        mime_type = str(args.get("mime_type") or "application/octet-stream")
        name = str(args.get("name") or "")
        ui = getattr(context, "ui", None) or getattr(session, "ui", None)

        # In a container worker the host owns the authoritative artifact
        # registry. Publishing over the authenticated control plane avoids
        # relying on a nested session bind mount being present and means web
        # and mobile can list the artifact immediately.
        host_published = (
            session_type == "container"
            and ui is not None
            and hasattr(ui, "publish_artifact")
        )
        if host_published:
            descriptor = ui.publish_artifact(
                name=name,
                source_path=source_path,
                content=content,
                mime_type=mime_type,
            )
        else:
            descriptor = _registry(context).add(
                name=name,
                source_path=source_path,
                content=content,
                mime_type=mime_type,
            )

        # Host-side container publishing already emits artifact_created after
        # the registry write. Other runtimes retain the existing live event.
        if not host_published:
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
    except (ArtifactError, OSError, RuntimeError) as exc:
        return json.dumps({"ok": False, "error": str(exc), "artifacts": []}, indent=2)


@tool(
    name="publish_visualization",
    description=(
        "Publish an interactive visualization as a sandboxed HTML artifact. "
        "Provide exactly one of html or file_path. Prefer a self-contained HTML "
        "document; CDN scripts are allowed but the page cannot access the parent "
        "chat. Web and mobile render it inline, while terminal clients receive a "
        "clickable browser link."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "HTML file name, normally ending in .html.",
            },
            "title": {
                "type": "string",
                "description": "Short title shown above the visualization.",
            },
            "html": {
                "type": "string",
                "description": "Complete UTF-8 HTML document.",
            },
            "file_path": {
                "type": "string",
                "description": "Existing HTML file in the current runtime.",
            },
            "height": {
                "type": "integer",
                "minimum": 180,
                "maximum": 1200,
                "default": 480,
                "description": "Preferred inline frame height in CSS pixels.",
            },
        },
        "required": ["name"],
    },
    requires_approval=False,
    execution_kind="mutate",
    preview_policy="none",
    group="artifact",
)
def publish_visualization_tool(args: dict[str, Any], context) -> str:
    try:
        source_path = _validated_source_path(args, context)
        html = args.get("html") if "html" in args else None
        if (source_path is None) == (html is None):
            raise ArtifactError("provide exactly one of html or file_path")
        if source_path is not None and not source_path.lower().endswith((".html", ".htm")):
            raise ArtifactError("visualization file_path must point to an HTML file")

        session = getattr(context, "session", None)
        variables = (
            getattr(session, "variables", None)
            or getattr(context, "variables", None)
            or {}
        )
        session_type = normalize_session_type(
            variables.get("session_type", "workspace")
        )
        ui = getattr(context, "ui", None) or getattr(session, "ui", None)
        name = str(args.get("name") or "visualization.html")
        title = str(args.get("title") or name)
        height = max(180, min(1200, int(args.get("height") or 480)))
        host_published = (
            session_type == "container"
            and ui is not None
            and hasattr(ui, "publish_artifact")
        )

        registry = None
        if host_published:
            descriptor = ui.publish_artifact(
                name=name,
                source_path=source_path,
                content=html,
                mime_type="text/html",
                kind="visualization",
                display="inline",
                title=title,
                height=height,
            )
        else:
            registry = _registry(context)
            descriptor = registry.add(
                name=name,
                source_path=source_path,
                content=html,
                mime_type="text/html",
                kind="visualization",
                display="inline",
                title=title,
                height=height,
            )

        if not host_published:
            try:
                if ui is not None and hasattr(ui, "publish"):
                    ui.publish({"kind": "artifact_created", "artifact": descriptor})
                elif ui is not None and hasattr(ui, "_publish"):
                    ui._publish({"kind": "artifact_created", "artifact": descriptor})
            except Exception:
                pass

        if ui is not None and hasattr(ui, "render_visualization"):
            local_path = (
                registry.resolve_path(descriptor["artifact_id"])
                if registry is not None
                else None
            )
            ui.render_visualization(descriptor, local_path=local_path)

        return json.dumps(
            {
                "ok": True,
                "message": f"Visualization ready: {descriptor['title']}",
                "artifact": descriptor,
                "artifacts": [descriptor],
            },
            indent=2,
        )
    except (ArtifactError, OSError, RuntimeError, TypeError, ValueError) as exc:
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
        session = getattr(context, "session", None)
        variables = (
            getattr(session, "variables", None)
            or getattr(context, "variables", None)
            or {}
        )
        session_type = normalize_session_type(
            variables.get("session_type", "workspace")
        )
        ui = getattr(context, "ui", None) or getattr(session, "ui", None)
        if session_type == "container" and ui is not None and hasattr(ui, "list_artifacts"):
            artifacts = ui.list_artifacts()
        else:
            artifacts = _registry(context).list()
        return json.dumps({"ok": True, "artifacts": artifacts}, indent=2)
    except (ArtifactError, OSError, RuntimeError) as exc:
        return json.dumps({"ok": False, "error": str(exc), "artifacts": []}, indent=2)
