"""Ownership-preserving atomic writes for bind-mounted state files.

Container workers run as root inside the container while the session
directory (``~/.mucli/sessions/<name>``) and trace directory are
bind-mounted from the host, where they belong to an unprivileged user.
``mkstemp`` + ``os.replace`` creates a *new* inode owned by root with
mode 0600, so after the first worker save the host process can no
longer read ``session.json`` and treats the session as nonexistent.

These helpers run only when the effective uid is 0 and are best-effort:
they never raise, so a failed ``chown`` cannot break a save.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

__all__ = ["match_owner", "preserve_owner", "target_owner"]


def _euid() -> int:
    try:
        return int(os.geteuid())
    except (AttributeError, OSError):  # pragma: no cover - non-POSIX
        return -1


def target_owner(target_path: str) -> Optional[Tuple[int, int, Optional[int]]]:
    """Return ``(uid, gid, mode)`` to apply for ``target_path``.

    Existing target wins (owner + mode preserved).  Otherwise fall back to
    the parent directory's owner with ``mode=None`` (caller keeps its own
    default mode).  ``None`` when neither can be stat'ed.
    """
    try:
        st = os.stat(target_path)
        return st.st_uid, st.st_gid, st.st_mode & 0o7777
    except OSError:
        pass
    try:
        parent = os.path.dirname(os.path.abspath(target_path)) or "."
        st = os.stat(parent)
        return st.st_uid, st.st_gid, None
    except OSError:
        return None


def match_owner(fd_or_path, target_path: str, *, default_mode: Optional[int] = None) -> bool:
    """Make ``fd_or_path`` (a staged temp file) look like ``target_path``.

    Only acts when running as root; a non-root process already creates
    files it owns.  Returns ``True`` when ownership was applied.
    """
    if _euid() != 0:
        return False
    owner = target_owner(target_path)
    if owner is None:
        return False
    uid, gid, mode = owner
    if mode is None:
        mode = default_mode
    try:
        if isinstance(fd_or_path, int):
            os.fchown(fd_or_path, uid, gid)
            if mode is not None:
                os.fchmod(fd_or_path, mode)
        else:
            os.chown(fd_or_path, uid, gid)
            if mode is not None:
                os.chmod(fd_or_path, mode)
        return True
    except OSError:
        return False


def preserve_owner(path: str) -> bool:
    """Chown an already-created file at ``path`` to its parent directory's owner.

    For append-only files (trace JSONL) that are opened directly rather than
    staged.  Root-only, best-effort, never raises.
    """
    if _euid() != 0:
        return False
    try:
        if os.stat(path).st_uid != 0:
            return False  # already non-root; nothing to fix
        parent = os.path.dirname(os.path.abspath(path)) or "."
        st = os.stat(parent)
        if st.st_uid == 0:
            return False  # host dir is root-owned too; leave alone
        os.chown(path, st.st_uid, st.st_gid)
        return True
    except OSError:
        return False
