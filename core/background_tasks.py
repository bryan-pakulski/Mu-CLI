"""Backward-compatible re-export shim.

The body of this module moved to `mu/tools/shell/background.py` during
the Phase 6 namespace rename. New code should import from
``mu.tools.shell.background``.
"""

from mu.tools.shell.background import *  # noqa: F401,F403
from mu.tools.shell.background import (  # noqa: F401
    BackgroundTask,
    BackgroundTaskRegistry,
    summarize_task,
    tail,
)
