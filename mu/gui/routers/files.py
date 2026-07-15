"""Workspace Files panel — navigate and edit session workspace files.

A first-class view panel backing the GUI "Files" view: a workspace-scoped
file tree plus read/write endpoints for the in-browser editor (CodeMirror 5).
Every mutating path funnels through :func:`_resolve_within`, which enforces
the same guarantees the agent's file tools already honor — but stronger:

* ``os.path.realpath`` resolves symlinks and ``..`` *before* the containment
  check (the agent's ``mu/tools/_bounds.py:check_bounds`` uses a plain
  ``str.startswith``, which a ``foo/../../etc`` traversal can defeat once the
  path is normalized — here we normalize first, then compare with
  ``os.path.commonpath``).
* :func:`mu.security.secret_paths.is_denied_path` refuses secret material
  (``.env``, ``*.pem``, ``~/.ssh``, ``/proc/*/environ``, …) regardless of
  workspace scope.
* :meth:`mu.workspace.folder_context.FolderContext.is_ignored` skips
  gitignored + hardcoded-ignored files so the tree matches what the agent
  sees.

Writes are atomic (temp file + ``os.replace``) and keep a ``.bak`` of the
prior content, so an in-browser edit can't silently clobber a file or leave
a half-written one. An optional ``expected_mtime`` gives optimistic
concurrency: if the file changed on disk since the editor read it (e.g. the
agent edited it under the user), save returns 409 and the editor prompts a
reload rather than overwriting.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from mu.security.secret_paths import is_denied_path

from ..deps import require_session

router = APIRouter()

# Soft caps so the editor never tries to slurp a binary blob or a log the
# size of the session. The agent's read_file/get_chunk already handle huge
# files; the editor is for normal-sized source.
_MAX_EDITABLE_BYTES = 1 * 1024 * 1024  # 1 MiB
_TREE_DEPTH = 2  # how many levels to pre-fetch for the top-level tree
_BACKUP_SUFFIX = ".bak"


# ---------------------------------------------------------------------------
# containment
# ---------------------------------------------------------------------------


class _PathError(HTTPException):
    """Raised by _resolve_within; carries the detail already formatted."""

    def __init__(self, detail: str, status: int = 400):
        super().__init__(status_code=status, detail=detail)


def _require_workspace(session) -> "Any":
    fc = getattr(session, "folder_context", None)
    folders = list(getattr(fc, "folders", []) or [])
    if not folders:
        raise HTTPException(
            status_code=409,
            detail=(
                "No workspace attached. Add a folder via the inspector or "
                "/workspace folder <path>."
            ),
        )
    return fc


def _resolve_within(
    raw_path: str, folder_context, *, allow_missing: bool = False
) -> Tuple[str, str]:
    """Resolve ``raw_path`` to an absolute, in-workspace, non-secret path.

    Returns ``(abs_real_path, matching_folder_root)``. Raises ``_PathError``
    (an HTTPException) on any rejection so callers can let it propagate
    straight to a 4xx response.

    ``allow_missing`` lets create/rename target a not-yet-existing path while
    still enforcing containment + secret rules on the *target*.
    """
    if not raw_path or not str(raw_path).strip():
        raise _PathError("path is required")

    folders = [os.path.realpath(f) for f in (getattr(folder_context, "folders", []) or [])]
    if not folders:
        raise _PathError("No workspace attached.", status=409)

    expanded = os.path.expanduser(str(raw_path).strip())
    # If the user gives a relative path, anchor it against the first
    # workspace root (the session's cwd is usually that anyway, but be
    # explicit so behavior doesn't depend on process cwd).
    if not os.path.isabs(expanded):
        expanded = os.path.join(folders[0], expanded)

    real = os.path.realpath(expanded)

    # Containment via commonpath — robust against ``..`` and symlinks because
    # we already realpath'd. The target must live at or beneath a workspace
    # root.
    contained_root = None
    for folder in folders:
        try:
            common = os.path.commonpath([real, folder])
        except ValueError:
            # commonpath raises when paths are on different drives (Windows);
            # just skip that root.
            continue
        if common == folder:
            contained_root = folder
            break
    if contained_root is None:
        raise _PathError(
            f"Path is outside the workspace: {real}", status=403
        )

    # Secret-path denylist (resolves symlinks itself; pass abs path).
    denied, reason = is_denied_path(real)
    if denied:
        raise _PathError(
            f"Refused: {reason or 'secret path'}", status=403
        )

    # gitignore / hardcoded ignores. Ignored paths are hidden from the tree
    # and refused for read/write. For a not-yet-existing target (create /
    # rename), check the parent directory instead — a brand-new file inside
    # an ignored dir is still refused.
    check_path = real
    if allow_missing and not os.path.exists(real):
        check_path = os.path.dirname(real)
    if check_path and folder_context.is_ignored(check_path):
        raise _PathError("Path is ignored (gitignore / excluded).", status=403)

    return real, contained_root


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------


def _entry_for(full: str) -> Dict[str, Any]:
    try:
        st = os.stat(full, follow_symlinks=False)
    except OSError:
        return {
            "name": os.path.basename(full),
            "path": full,
            "is_dir": os.path.isdir(full),
            "size": None,
        }
    is_dir = os.path.isdir(full)
    return {
        "name": os.path.basename(full),
        "path": full,
        "is_dir": is_dir,
        "size": None if is_dir else st.st_size,
    }


def _list_dir(folder_context, dir_path: str, depth: int) -> list:
    """One-level listing of ``dir_path``, filtered, dirs first.

    ``depth`` is the remaining depth budget; when > 0, directories are
    pre-fetched with their own ``children`` so the top-level tree is
    navigable without a round-trip per expand. ``depth == 0`` leaves
    directory ``children`` as ``null`` (lazy — fetched on expand).
    """
    entries = []
    try:
        names = os.listdir(dir_path)
    except (PermissionError, OSError):
        return entries
    for name in names:
        full = os.path.join(dir_path, name)
        if folder_context.is_ignored(full):
            continue
        ent = _entry_for(full)
        if ent["is_dir"]:
            if depth > 0:
                ent["children"] = _list_dir(folder_context, full, depth - 1)
            else:
                ent["children"] = None
        entries.append(ent)
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


@router.get("/tree")
async def get_tree(
    request: Request,
    path: str = Query(default=""),
) -> Dict[str, Any]:
    """Workspace file tree.

    With no ``path``: a depth-limited tree per attached workspace root.
    With ``path``: a single-level listing of that directory (lazy expand),
    containment-checked. Returns ``{roots: [...]}`` (top-level) or
    ``{path, entries: [...]}`` (subtree).
    """
    session = request.app.state.session_by_name()
    fc = _require_workspace(session)

    if path:
        real, _root = _resolve_within(path, fc)
        if not os.path.isdir(real):
            raise HTTPException(status_code=400, detail=f"not a directory: {real}")
        return {"path": real, "entries": _list_dir(fc, real, 0)}

    roots = []
    for folder in fc.folders:
        real = os.path.realpath(folder)
        if not os.path.isdir(real):
            continue
        roots.append(
            {
                "name": os.path.basename(real) or real,
                "path": real,
                "is_dir": True,
                "children": _list_dir(fc, real, _TREE_DEPTH - 1),
            }
        )
    return {"roots": roots}


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


@router.get("/read")
async def read_file(
    request: Request,
    path: str = Query(default=""),
) -> Dict[str, Any]:
    """Read a workspace file for editing.

    Returns ``{path, content, size, readonly, why?}``. Non-text and oversized
    files come back ``readonly`` with a reason instead of being refused, so
    the editor can show the file's existence in the tree and a banner rather
    than a hard 403. Secret / out-of-workspace paths are refused (403).
    """
    session = request.app.state.session_by_name()
    fc = _require_workspace(session)
    real, _root = _resolve_within(path, fc)

    if not os.path.exists(real):
        raise HTTPException(status_code=404, detail=f"not found: {real}")
    if os.path.isdir(real):
        raise HTTPException(status_code=400, detail="path is a directory")

    try:
        size = os.path.getsize(real)
    except OSError:
        size = 0

    if size > _MAX_EDITABLE_BYTES:
        return {
            "path": real,
            "content": "",
            "size": size,
            "readonly": True,
            "why": "file too large to edit in the browser",
        }
    if not fc._is_text_file(real):
        return {
            "path": real,
            "content": "",
            "size": size,
            "readonly": True,
            "why": "binary file",
        }

    try:
        with open(real, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        raise HTTPException(status_code=403, detail=f"could not read: {exc}")

    try:
        mtime = os.path.getmtime(real)
    except OSError:
        mtime = None

    return {
        "path": real,
        "content": content,
        "size": size,
        "readonly": False,
        "why": "",
        "mtime": mtime,
    }


# ---------------------------------------------------------------------------
# write helpers
# ---------------------------------------------------------------------------


def _atomic_write(real: str, content: str, *, backup_existing: bool) -> int:
    """Write ``content`` to ``real`` atomically, keeping a ``.bak``.

    Returns the new size. The prior content (if the file existed) is copied
    to ``real + ".bak"`` first; the new content goes to a temp file in the
    same directory then ``os.replace`` swaps it in, so a crash mid-write
    leaves the original intact.
    """
    directory = os.path.dirname(real) or "."
    if backup_existing and os.path.exists(real):
        try:
            shutil.copy2(real, real + _BACKUP_SUFFIX)
        except OSError:
            # Best-effort backup; never block the write on it.
            pass

    tmp = os.path.join(directory, f".{os.path.basename(real)}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, real)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return os.path.getsize(real)


@router.post("/save")
async def save_file(
    request: Request,
    payload: Dict[str, Any],
    session=Depends(require_session),
) -> Dict[str, Any]:
    """Save editor content to a workspace file (atomic, with backup)."""
    fc = _require_workspace(session)

    raw_path = str(payload.get("path") or "").strip()
    content = payload.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="content is required")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")

    # The save target may already exist (an edit) or be brand-new (a file
    # just created via /create then edited). allow_missing so a not-yet-
    # existing path is accepted, but containment + secret rules still apply.
    real, _root = _resolve_within(raw_path, fc, allow_missing=True)

    if os.path.isdir(real):
        raise HTTPException(status_code=400, detail="path is a directory")

    # Optimistic concurrency: if the caller supplied the mtime it read the
    # file at and the on-disk mtime differs, refuse so the user reloads.
    expected_mtime = payload.get("expected_mtime")
    if expected_mtime is not None and os.path.exists(real):
        try:
            current_mtime = os.path.getmtime(real)
        except OSError:
            current_mtime = None
        if current_mtime is not None and abs(float(expected_mtime) - current_mtime) > 1e-6:
            raise HTTPException(
                status_code=409,
                detail="File changed on disk since you opened it — reload and retry.",
            )

    backup_existing = os.path.exists(real)
    try:
        size = _atomic_write(real, content, backup_existing=backup_existing)
    except OSError as exc:
        raise HTTPException(status_code=403, detail=f"could not write: {exc}")

    try:
        fc.track_file(real)
    except Exception:
        pass
    try:
        fc.sync_with_filesystem()
    except Exception:
        pass

    try:
        mtime = os.path.getmtime(real)
    except OSError:
        mtime = None

    return {"ok": True, "path": real, "size": size, "mtime": mtime}


# ---------------------------------------------------------------------------
# create / rename / delete
# ---------------------------------------------------------------------------


@router.post("/create")
async def create_entry(
    request: Request,
    payload: Dict[str, Any],
    session=Depends(require_session),
) -> Dict[str, Any]:
    """Create a new file (empty) or directory under the workspace."""
    fc = _require_workspace(session)

    raw_path = str(payload.get("path") or "").strip()
    is_dir = bool(payload.get("is_dir"))
    if not raw_path:
        raise HTTPException(status_code=400, detail="path is required")

    real, _root = _resolve_within(raw_path, fc, allow_missing=True)
    if os.path.exists(real):
        raise HTTPException(status_code=409, detail="already exists: " + real)

    try:
        if is_dir:
            os.makedirs(real, exist_ok=False)
        else:
            os.makedirs(os.path.dirname(real) or ".", exist_ok=True)
            with open(real, "w", encoding="utf-8") as f:
                f.write("")
    except OSError as exc:
        raise HTTPException(status_code=403, detail=f"could not create: {exc}")

    if not is_dir:
        try:
            fc.track_file(real)
        except Exception:
            pass

    return {
        "ok": True,
        "path": real,
        "is_dir": is_dir,
    }


@router.post("/rename")
async def rename_entry(
    request: Request,
    payload: Dict[str, Any],
    session=Depends(require_session),
) -> Dict[str, Any]:
    """Rename/move a workspace file or directory (containment-checked both sides)."""
    fc = _require_workspace(session)

    src = str(payload.get("from") or "").strip()
    dst = str(payload.get("to") or "").strip()
    if not src or not dst:
        raise HTTPException(status_code=400, detail="'from' and 'to' are required")

    src_real, _ = _resolve_within(src, fc)
    dst_real, _ = _resolve_within(dst, fc, allow_missing=True)
    if not os.path.exists(src_real):
        raise HTTPException(status_code=404, detail="not found: " + src_real)
    if os.path.exists(dst_real):
        raise HTTPException(status_code=409, detail="already exists: " + dst_real)

    # Best-effort backup of the source before the move.
    try:
        if os.path.isfile(src_real):
            shutil.copy2(src_real, src_real + _BACKUP_SUFFIX)
    except OSError:
        pass

    try:
        os.rename(src_real, dst_real)
    except OSError as exc:
        raise HTTPException(status_code=403, detail=f"could not rename: {exc}")

    try:
        fc.sync_with_filesystem()
    except Exception:
        pass

    return {"ok": True, "from": src_real, "to": dst_real}


@router.delete("")
async def delete_entry(
    request: Request,
    path: str = Query(default=""),
    recursive: bool = Query(default=False),
    session=Depends(require_session),
) -> Dict[str, Any]:
    """Delete a workspace file or directory.

    Directories must be empty unless ``recursive=true`` is passed (the
    frontend asks for confirmation before sending recursive). A ``.bak`` of
    a deleted file is kept so a mistaken delete is recoverable.
    """
    fc = _require_workspace(session)

    real, _root = _resolve_within(path, fc)
    if not os.path.exists(real):
        raise HTTPException(status_code=404, detail="not found: " + real)

    # Refuse to delete a workspace root itself.
    roots = {os.path.realpath(f) for f in fc.folders}
    if real in roots:
        raise HTTPException(
            status_code=400, detail="Refusing to delete a workspace root."
        )

    if os.path.isdir(real):
        if not recursive and os.listdir(real):
            raise HTTPException(
                status_code=409,
                detail="Directory not empty — pass recursive=true to delete anyway.",
            )
        try:
            if recursive:
                shutil.rmtree(real)
            else:
                os.rmdir(real)
        except OSError as exc:
            raise HTTPException(status_code=403, detail=f"could not delete: {exc}")
    else:
        try:
            shutil.copy2(real, real + _BACKUP_SUFFIX)
        except OSError:
            pass
        try:
            os.unlink(real)
        except OSError as exc:
            raise HTTPException(status_code=403, detail=f"could not delete: {exc}")

    try:
        fc.sync_with_filesystem()
    except Exception:
        pass

    return {"ok": True, "path": real}