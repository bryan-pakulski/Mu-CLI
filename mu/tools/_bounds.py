"""Filesystem boundary checks shared by file and shell tools.

Workspace sessions remain constrained to explicitly attached host folders and
respect their ignore rules.  Container sessions are different: the Docker
sandbox itself is the execution boundary, so tools may inspect and modify any
path that exists inside the container, including system directories and all
mounted paths.

Secret-path denial remains unconditional in every session type.  Removing the
workspace boundary must never expose SSH keys, cloud credentials, provider
configuration, private key material, shell histories, or MuCLI's credential
state.
"""

from __future__ import annotations

import os
from typing import Any

from mu.tools.capabilities import normalize_session_type
from utils.logger import logger


def _secret_allowed(filename: str) -> bool:
    from mu.security.secret_paths import is_denied_path

    denied, reason = is_denied_path(filename)
    if denied:
        logger.warning("check_bounds: blocked secret path %r: %s", filename, reason)
        return False
    return True


def check_bounds(
    filename: str,
    folder_context: Any,
    *,
    session_type: str = "workspace",
) -> bool:
    """Return whether ``filename`` is reachable by filesystem tools.

    * Secret paths are always refused.
    * Container sessions may access the complete container filesystem.
    * Workspace sessions remain confined to attached folders and ignore rules.
    * A workspace-less non-container runtime retains the historical behaviour:
      the filesystem boundary is open, with the secret denylist as the floor.
    """
    if not _secret_allowed(filename):
        return False

    if normalize_session_type(session_type) == "container":
        return True

    if not folder_context or not folder_context.folders:
        return True

    abs_path = os.path.realpath(os.path.abspath(os.path.expanduser(filename)))
    for folder in folder_context.folders:
        root = os.path.realpath(os.path.abspath(os.path.expanduser(str(folder))))
        try:
            contained = os.path.commonpath([abs_path, root]) == root
        except ValueError:
            contained = False
        if not contained:
            continue
        if folder_context.is_ignored(abs_path):
            return False
        return True
    return False


def default_working_directory(folder_context: Any, session_type: str) -> str:
    """Choose a useful cwd without turning mounts into a container boundary."""
    if normalize_session_type(session_type) == "container":
        configured = os.environ.get("MUCLI_CONTAINER_CWD", "").strip()
        if configured and os.path.isdir(configured) and _secret_allowed(configured):
            return configured
        if os.path.isdir("/workspace"):
            return "/workspace"
        return os.getcwd()
    if folder_context and folder_context.folders:
        return str(folder_context.folders[0])
    return os.getcwd()


__all__ = ["check_bounds", "default_working_directory"]
