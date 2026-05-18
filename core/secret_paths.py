"""Backward-compatible re-export shim.

The body of this module moved to `mu/security/secret_paths.py` during
the Phase 6 namespace rename. New code should import from
``mu.security.secret_paths``.
"""

from mu.security.secret_paths import *  # noqa: F401,F403
from mu.security.secret_paths import (  # noqa: F401
    extract_paths_from_command,
    is_denied_path,
    redact_secrets,
)
