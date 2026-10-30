"""Where a new job's code comes from: a job payload's ``source``.

::

    {"kind": "session", "session": "<name>", "include_conversation": false}
    {"kind": "repository", "name": "<Settings → Repositories row>"}   (or "url": "<git URL or path>")
    {"kind": "folder", "path": "/abs/folder"}                          (git or not)

:func:`apply_source` turns it into the fields job creation already understands
(``session_name`` / ``repository`` / ``workspace_root`` / ``base_branch``), records
``metadata.source`` and, for ``include_conversation``, adds the session's conversation to
``attachments``. Payloads without ``source`` pass through unchanged, so the older
``workspace_root`` / ``repository`` / ``session_name`` fields keep working.

The repository list is the ``jobs.repositories`` rule (Settings → Repositories): rows of
``name``, ``url`` (a git URL or a local path) and an optional ``branch``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

KINDS = ("session", "repository", "folder")


def configured_repositories() -> List[Dict[str, str]]:
    from .job_settings import rule_or
    rows = rule_or("jobs.repositories", []) or []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get("name") or "").strip() and str(row.get("url") or "").strip():
            out.append({"name": str(row["name"]).strip(), "url": str(row["url"]).strip(),
                        "branch": str(row.get("branch") or "").strip()})
    return out


def session_workspace(name: str) -> str:
    """First workspace folder of a saved session ('' when it has none)."""
    from .attachments import _session_dir
    try:
        with open(os.path.join(_session_dir(name), "session.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    folders = (dict((data or {}).get("folder_context") or {}).get("folders") or []) if isinstance(data, dict) else []
    return str(folders[0]) if folders else ""


def apply_source(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve ``payload['source']`` (raises ``ValueError``); idempotent, drops ``source``."""
    value = dict(payload or {})
    source = value.pop("source", None)
    if source in (None, ""):
        return value
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    kind = str(source.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"source.kind must be one of {', '.join(KINDS)}")
    if value.get("repository") or value.get("repo") or (kind != "session" and value.get("workspace_root")):
        raise ValueError("pass either 'source' or 'workspace_root'/'repository', not both")
    metadata = dict(value.get("metadata") or {})
    if kind == "session":
        name = str(source.get("session") or value.get("session_name") or "").strip()
        if not name:
            raise ValueError("source.session is required")
        from .attachments import _session_dir
        _session_dir(name)  # exists and is a valid name
        workspace = str(value.get("workspace_root") or "").strip() or session_workspace(name)
        if not workspace:
            raise ValueError(f"session '{name}' has no workspace; choose a folder or repository instead")
        value["session_name"] = name
        value["workspace_root"] = workspace
        metadata["source"] = {"kind": "session", "session": name}
        if source.get("include_conversation"):
            value["attachments"] = [*list(value.get("attachments") or []), {"session": name, "conversation": True}]
    elif kind == "repository":
        name = str(source.get("name") or "").strip()
        url = str(source.get("url") or "").strip()
        branch = str(source.get("branch") or "").strip()
        if name:
            row = next((r for r in configured_repositories() if r["name"] == name), None)
            if row is None:
                raise ValueError(f"no repository named '{name}' in Settings → Repositories")
            url, branch = row["url"], branch or row["branch"]
        if not url:
            raise ValueError("source.name or source.url is required")
        from .clone import looks_like_url
        if looks_like_url(url):
            value["repository"] = f"{url}#{branch}" if branch and "#" not in url else url
        else:
            value["workspace_root"] = os.path.abspath(os.path.expanduser(url))
            if branch and not str(value.get("base_branch") or "").strip():
                value["base_branch"] = branch
        metadata["source"] = {"kind": "repository", **({"name": name} if name else {}), "url": url,
                              **({"branch": branch} if branch else {})}
    else:
        path = str(source.get("path") or "").strip()
        if not path:
            raise ValueError("source.path is required")
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            raise ValueError(f"folder {path} does not exist")
        value["workspace_root"] = path
        metadata["source"] = {"kind": "folder", "path": path}
    value["metadata"] = metadata
    return value
