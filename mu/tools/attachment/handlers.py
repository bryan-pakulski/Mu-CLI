
"""Agent tools for discovering and reading user-uploaded documents."""
from __future__ import annotations

import json
import os
import shutil
from typing import Any

from mu.attachment import AttachmentError
from mu.attachment.extract import extract_text, read_chunk, search_text
from mu.tools import tool
from mu.tools.capabilities import session_type_from_context


def _registry(context):
    session = getattr(context, "session", None)
    manager = getattr(session, "session_manager", None)
    registry = getattr(session, "attachment_registry", None) or getattr(
        manager, "attachment_registry", None
    )
    if registry is None and session is not None and hasattr(session, "sync_runtime_state"):
        session.sync_runtime_state()
        registry = getattr(session, "attachment_registry", None)
    if registry is None:
        raise AttachmentError("attachment registry is unavailable for this session")
    return registry


@tool(
    name="list_attachments",
    description=(
        "List files uploaded by the user to this session. Use this whenever the user "
        "mentions an attached/uploaded document or when an attachment ID is needed."
    ),
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    group="attachment",
)
def list_attachments_tool(_args: dict[str, Any], context) -> str:
    try:
        return json.dumps({"ok": True, "attachments": _registry(context).list()}, indent=2)
    except (AttachmentError, OSError, RuntimeError) as exc:
        return json.dumps({"ok": False, "error": str(exc), "attachments": []}, indent=2)


@tool(
    name="read_attachment",
    description=(
        "Extract a bounded text chunk from a user-uploaded attachment by attachment_id. "
        "Supports text/code/log files, PDF, HTML, JSON, and DOCX. Use offset to continue."
    ),
    parameters={
        "type": "object",
        "properties": {
            "attachment_id": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "max_chars": {"type": "integer", "minimum": 500, "maximum": 50000, "default": 12000},
        },
        "required": ["attachment_id"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
    group="attachment",
)
def read_attachment_tool(args: dict[str, Any], context) -> str:
    try:
        registry = _registry(context)
        attachment_id = str(args.get("attachment_id") or "").strip()
        descriptor = registry.get(attachment_id)
        path = registry.resolve_path(attachment_id)
        if descriptor is None or path is None:
            raise AttachmentError("attachment not found")
        payload = read_chunk(
            path,
            str(descriptor.get("mime_type") or ""),
            offset=int(args.get("offset") or 0),
            max_chars=int(args.get("max_chars") or 12000),
        )
        return json.dumps({"ok": True, "attachment": descriptor, **payload}, ensure_ascii=False, indent=2)
    except (AttachmentError, OSError, RuntimeError, ValueError, TypeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, indent=2)


@tool(
    name="download_attachment",
    description=(
        "Copy a user-uploaded attachment into the local workspace or container "
        "filesystem so it can be processed with normal file and shell tools. Use "
        "this for ZIP archives, large documents, media, or files that must be split, "
        "converted, extracted, or inspected without placing their contents in the prompt."
    ),
    parameters={
        "type": "object",
        "properties": {
            "attachment_id": {"type": "string"},
            "destination": {
                "type": "string",
                "description": (
                    "Destination file or directory. Relative paths resolve from the "
                    "first workspace folder, or the container working directory. "
                    "Defaults to the attachment file name."
                ),
            },
            "overwrite": {"type": "boolean", "default": False},
        },
        "required": ["attachment_id"],
    },
    requires_approval=True,
    execution_kind="mutate",
    preview_policy="optional",
    group="attachment",
)
def download_attachment_tool(args: dict[str, Any], context) -> str:
    try:
        session_type = session_type_from_context(context)
        if session_type not in {"workspace", "container"}:
            raise AttachmentError(
                "download_attachment is available only in workspace or container sessions"
            )

        registry = _registry(context)
        attachment_id = str(args.get("attachment_id") or "").strip()
        descriptor = registry.get(attachment_id)
        source = registry.resolve_path(attachment_id)
        if descriptor is None or source is None:
            raise AttachmentError("attachment not found")

        folder_context = getattr(context, "folder_context", None)
        folders = list(getattr(folder_context, "folders", None) or [])
        base_dir = os.path.realpath(
            os.path.abspath(os.path.expanduser(folders[0] if folders else os.getcwd()))
        )
        raw_destination = str(
            args.get("destination") or descriptor.get("name") or "attachment"
        )
        expanded = os.path.expanduser(raw_destination)
        destination = (
            os.path.realpath(os.path.abspath(expanded))
            if os.path.isabs(expanded)
            else os.path.realpath(os.path.join(base_dir, expanded))
        )
        if raw_destination.endswith(("/", os.sep)) or os.path.isdir(destination):
            destination = os.path.realpath(
                os.path.join(destination, str(descriptor.get("name") or "attachment"))
            )

        if session_type == "workspace":
            roots = [
                os.path.realpath(os.path.abspath(os.path.expanduser(str(folder))))
                for folder in folders
            ]
            if not roots:
                raise AttachmentError("workspace session has no attached folder")
            allowed = False
            for root in roots:
                try:
                    allowed = os.path.commonpath([destination, root]) == root
                except ValueError:
                    allowed = False
                if allowed:
                    break
            if not allowed:
                raise AttachmentError("destination must be inside an attached workspace")

        if os.path.exists(destination) and not bool(args.get("overwrite", False)):
            raise AttachmentError(
                f"destination already exists: {destination}; set overwrite=true to replace it"
            )
        os.makedirs(os.path.dirname(destination) or base_dir, exist_ok=True)
        shutil.copy2(source, destination)
        return json.dumps(
            {
                "ok": True,
                "message": f"Attachment copied to {destination}",
                "attachment": descriptor,
                "path": destination,
                "size": os.path.getsize(destination),
            },
            indent=2,
        )
    except (AttachmentError, OSError, RuntimeError, ValueError, TypeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, indent=2)


@tool(
    name="search_attachments",
    description=(
        "Search extracted text across user-uploaded attachments. Returns attachment IDs, "
        "names, match offsets, and surrounding snippets."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "attachment_ids": {"type": "array", "items": {"type": "string"}},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12},
        },
        "required": ["query"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
    group="attachment",
)
def search_attachments_tool(args: dict[str, Any], context) -> str:
    try:
        query = str(args.get("query") or "").strip()
        if not query:
            raise AttachmentError("query is required")
        registry = _registry(context)
        requested = {str(value) for value in (args.get("attachment_ids") or []) if value}
        maximum = max(1, min(50, int(args.get("max_results") or 12)))
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for descriptor in registry.list():
            attachment_id = str(descriptor.get("attachment_id") or "")
            if requested and attachment_id not in requested:
                continue
            path = registry.resolve_path(attachment_id)
            if path is None:
                continue
            try:
                text, format_name = extract_text(path, str(descriptor.get("mime_type") or ""))
                for match in search_text(text, query, max_results=maximum - len(results)):
                    results.append({
                        "attachment_id": attachment_id,
                        "name": descriptor.get("name"),
                        "format": format_name,
                        **match,
                    })
                    if len(results) >= maximum:
                        break
            except (OSError, ValueError) as exc:
                errors.append({"attachment_id": attachment_id, "name": str(descriptor.get("name") or ""), "error": str(exc)})
            if len(results) >= maximum:
                break
        return json.dumps({"ok": True, "query": query, "results": results, "errors": errors}, ensure_ascii=False, indent=2)
    except (AttachmentError, OSError, RuntimeError, ValueError, TypeError) as exc:
        return json.dumps({"ok": False, "error": str(exc), "results": []}, indent=2)
