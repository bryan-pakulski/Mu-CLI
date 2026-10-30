"""Files attached to a job at creation (the New job sheet, ``POST /api/jobs``).

A job payload's ``attachments`` is a list of items, each one of::

    {"name": "spec.pdf", "content_base64": "..."}           an uploaded file
    {"session": "<name>", "attachment_id": "..."}           a chat session's attachment
    {"session": "<name>", "artifact_id": "..."}             a chat session's artifact
    {"session": "<name>", "conversation": true}             the session's conversation as Markdown

Every item is resolved and size-checked before the job exists, so a bad item fails
creation with a 400 instead of producing a job with missing inputs. Files are stored under
``$MUCLI_HOME/jobs/<id>/attachments/`` and ``job.metadata.attachments`` lists them. Each
sandboxed stage gets a copy read-only at ``/artifacts/attachments/`` and its prompt names
them (:func:`prompt_lines`). Attachments never enter the worktree, so they are never
committed or pushed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

MAX_FILES = 20
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
CONVERSATION_MAX_CHARS = 400_000
SANDBOX_DIR = "/artifacts/attachments"
FOLDER = "attachments"

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass
class PendingAttachment:
    name: str
    data: Optional[bytes] = None      # inline content (uploads, conversations)
    path: str = ""                    # or a file to copy (session attachments/artifacts)
    mime_type: str = ""
    origin: Optional[Dict[str, str]] = None

    @property
    def size(self) -> int:
        return len(self.data) if self.data is not None else os.path.getsize(self.path)


def safe_name(name: str) -> str:
    """A flat, portable file name (no directories, no leading dot)."""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r"[^\w.+-]+", "_", base).lstrip(".")
    if len(base) > 120:
        stem, ext = os.path.splitext(base)
        base = stem[:120 - len(ext[:20])] + ext[:20]
    return base or "attachment"


def _sessions_root() -> str:
    from utils.config import HISTORY_DIR
    return os.path.join(HISTORY_DIR, "sessions")


def _session_dir(name: str) -> str:
    session = str(name or "").strip()
    if not _SESSION_RE.match(session):
        raise ValueError(f"invalid session name {session!r}")
    path = os.path.join(_sessions_root(), session)
    if not os.path.isdir(path):
        raise ValueError(f"session '{session}' does not exist")
    return path


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") for p in content
                         if isinstance(p, dict) and p.get("type") in (None, "text") and p.get("text"))
    return ""


def conversation_markdown(session: str) -> str:
    """The session's conversation summary plus its user/assistant text turns (no tool output).

    Long conversations keep their most recent turns within :data:`CONVERSATION_MAX_CHARS`."""
    path = os.path.join(_session_dir(session), "session.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"session '{session}' could not be read: {exc}") from exc
    history = data if isinstance(data, list) else list((data or {}).get("history") or [])
    summary = str((data or {}).get("conversation_summary") or "").strip() if isinstance(data, dict) else ""
    turns: List[str] = []
    for message in history:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            continue
        text = _text(message.get("content")).strip()
        if text:
            turns.append(f"## {message['role'].capitalize()}\n\n{text}\n")
    head = f"# Conversation from session {session}\n"
    if summary:
        head += f"\n## Summary of earlier turns\n\n{summary}\n"
    budget = CONVERSATION_MAX_CHARS - len(head)
    kept: List[str] = []
    for turn in reversed(turns):
        if budget - len(turn) < 0:
            kept.append("_(older turns omitted)_\n")
            break
        kept.append(turn)
        budget -= len(turn)
    if not turns and not summary:
        raise ValueError(f"session '{session}' has no conversation to attach")
    return head + "\n" + "\n".join(reversed(kept))


def _registry_file(session: str, key: str, item_id: str) -> PendingAttachment:
    session_dir = _session_dir(session)
    if key == "attachment_id":
        from mu.attachment import AttachmentRegistry
        registry: Any = AttachmentRegistry(session_dir)
        label = "attachment"
    else:
        from mu.artifact import ArtifactRegistry
        registry = ArtifactRegistry(session_dir)
        label = "artifact"
    descriptor = registry.get(item_id)
    path = registry.resolve_path(item_id) if descriptor else None
    if not descriptor or not path or not os.path.isfile(path):
        raise ValueError(f"session '{session}' has no {label} {item_id!r}")
    return PendingAttachment(name=str(descriptor.get("name") or label), path=path,
                             mime_type=str(descriptor.get("mime_type") or ""),
                             origin={"session": session, key: str(item_id)})


def resolve(items: Any) -> List[PendingAttachment]:
    """Validate a payload's ``attachments`` into files ready to store (raises ``ValueError``)."""
    if items in (None, ""):
        return []
    if not isinstance(items, list):
        raise ValueError("attachments must be a list")
    if len(items) > MAX_FILES:
        raise ValueError(f"a job takes at most {MAX_FILES} attachments")
    out: List[PendingAttachment] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"attachments[{index}] must be an object")
        session = str(item.get("session") or "").strip()
        if "content_base64" in item:
            name = str(item.get("name") or "").strip()
            if not name:
                raise ValueError(f"attachments[{index}]: name is required")
            raw = str(item.get("content_base64") or "")
            if len(raw) > (MAX_FILE_BYTES * 4) // 3 + 8:
                raise ValueError(f"attachment {name!r} exceeds {MAX_FILE_BYTES // (1024 * 1024)} MiB")
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(f"attachment {name!r} is not valid base64") from exc
            pending = PendingAttachment(name=name, data=data, mime_type=str(item.get("mime_type") or ""),
                                        origin={"kind": "upload"})
        elif session and item.get("attachment_id"):
            pending = _registry_file(session, "attachment_id", str(item["attachment_id"]))
        elif session and item.get("artifact_id"):
            pending = _registry_file(session, "artifact_id", str(item["artifact_id"]))
        elif session and item.get("conversation"):
            pending = PendingAttachment(name=f"conversation-{session}.md",
                                        data=conversation_markdown(session).encode("utf-8"),
                                        mime_type="text/markdown", origin={"session": session, "kind": "conversation"})
        else:
            raise ValueError(f"attachments[{index}] needs content_base64, or a session with "
                             "attachment_id, artifact_id or conversation")
        if pending.size > MAX_FILE_BYTES:
            raise ValueError(f"attachment {pending.name!r} exceeds {MAX_FILE_BYTES // (1024 * 1024)} MiB")
        out.append(pending)
    if sum(p.size for p in out) > MAX_TOTAL_BYTES:
        raise ValueError(f"attachments exceed {MAX_TOTAL_BYTES // (1024 * 1024)} MiB in total")
    return out


def store(job_id: str, pending: List[PendingAttachment], *, root: Optional[str] = None) -> List[Dict[str, Any]]:
    """Write ``pending`` into the job's artifact folder; returns the ``metadata.attachments`` list."""
    if not pending:
        return []
    from .core.artifacts import artifacts_for
    folder = artifacts_for(job_id, root).path(FOLDER)
    os.makedirs(folder, exist_ok=True)
    used: set = set()
    out: List[Dict[str, Any]] = []
    for item in pending:
        name = safe_name(item.name)
        stem, ext = os.path.splitext(name)
        counter = 2
        while name.lower() in used:
            name = f"{stem}-{counter}{ext}"
            counter += 1
        used.add(name.lower())
        target = os.path.join(folder, name)
        if item.data is not None:
            with open(target, "wb") as handle:
                handle.write(item.data)
        else:
            shutil.copyfile(item.path, target)
        digest = hashlib.sha256()
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        out.append({"name": name, "size": os.path.getsize(target), "sha256": digest.hexdigest(),
                    "mime_type": item.mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream",
                    **({"origin": dict(item.origin)} if item.origin else {})})
    return out


def discard(job_id: str, *, root: Optional[str] = None) -> None:
    from .core.artifacts import artifacts_for
    shutil.rmtree(artifacts_for(job_id, root).path(FOLDER), ignore_errors=True)


def listed(job: Any) -> List[Dict[str, Any]]:
    return [dict(a) for a in ((getattr(job, "metadata", None) or {}).get("attachments") or []) if isinstance(a, dict)]


def path_for(job: Any, name: str, *, root: Optional[str] = None) -> Optional[str]:
    """Stored file of an attachment the job lists (``None`` for unknown names)."""
    if name not in {a.get("name") for a in listed(job)}:
        return None
    from .core.artifacts import artifacts_for
    path = artifacts_for(job, root).path(FOLDER, safe_name(name))
    return path if os.path.isfile(path) else None


def copy_into(job: Any, directory: str, *, root: Optional[str] = None) -> int:
    """Copy the job's attachments into a stage input folder (mounted at ``/artifacts``)."""
    count = 0
    for item in listed(job):
        source = path_for(job, str(item.get("name") or ""), root=root)
        if source:
            os.makedirs(os.path.join(directory, FOLDER), exist_ok=True)
            shutil.copyfile(source, os.path.join(directory, FOLDER, os.path.basename(source)))
            count += 1
    return count


def _size(value: int) -> str:
    for unit in ("B", "KB", "MB"):
        if value < 1024 or unit == "MB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024  # type: ignore[assignment]
    return f"{value} B"


def prompt_lines(job: Any) -> List[str]:
    """Prompt lines naming the attached files and where the stage finds them."""
    items = listed(job)
    if not items:
        return []
    lines = ["", f"Attached files (read-only, in {SANDBOX_DIR}/; read the ones relevant to the task):"]
    for item in items:
        lines.append(f"- {SANDBOX_DIR}/{item.get('name')} ({_size(int(item.get('size') or 0))}, "
                     f"{item.get('mime_type') or 'file'})")
    return lines
