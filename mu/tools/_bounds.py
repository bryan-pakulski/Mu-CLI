"""Workspace + secret-path containment check.

Every file-touching tool routes through `check_bounds(path, folder_context)`
before doing anything. Two checks, in order:

  1. **Secret-path denylist** — unconditional. Denied paths (~/.ssh, /etc/shadow,
     ~/.aws, .env*, *.pem, etc.) are refused even when no workspace is attached.
     See `core/secret_paths.py:is_denied_path`. There is no per-call override
     at this layer; bash callers can opt in to relaxed checks via the
     `security_allow_secret_paths` session variable.

  2. **Workspace containment** — when a folder_context with attached folders
     is present, the resolved abs path must start with one of those folders
     and not match the workspace's `.gitignore`. When no workspace is
     attached, this check is bypassed (the denylist is still the floor).

The legacy entry point at `core.tools._check_bounds` re-exports
`check_bounds` so callers that imported the underscored name keep working.
"""

from __future__ import annotations

import os
from typing import Any

from utils.logger import logger


def check_bounds(filename: str, folder_context: Any) -> bool:
    """Return True iff `filename` is reachable by the workspace tools.

    Refuses denylisted secret paths unconditionally, then (if a workspace
    is attached) enforces folder containment and `.gitignore` filtering.
    """
    from core.secret_paths import is_denied_path

    # Secret-path denials are unconditional at the file layer — there is
    # no override here. Bash callers that legitimately need a denylisted
    # path can opt in via `security_allow_secret_paths`.
    denied, reason = is_denied_path(filename)
    if denied:
        logger.warning(
            f"check_bounds: blocked secret path {filename!r}: {reason}"
        )
        return False

    if not folder_context or not folder_context.folders:
        # No workspace attached → boundary check is a no-op. The denylist
        # above is the floor of protection in this case.
        return True

    abs_path = os.path.abspath(os.path.expanduser(filename))

    within_bounds = False
    for f in folder_context.folders:
        if abs_path.startswith(os.path.abspath(f)):
            within_bounds = True
            break

    if not within_bounds:
        return False

    if folder_context.is_ignored(abs_path):
        return False

    return True


__all__ = ["check_bounds"]
