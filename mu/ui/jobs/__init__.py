"""Full-screen terminal job dashboard (planos TUI for mucli jobs).

``/jobs`` (or ``/job`` with no arguments, or ``/job ui``) opens it from the
terminal REPL. Views render to plain lines (``views.render``) so they are
testable without a TTY; ``run.run_jobs_tui`` is the prompt_toolkit shell.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional


def can_open_tui(session: Any = None, *, allow_prompt: bool = True) -> bool:
    """Only from an interactive terminal REPL (never the web GUI or tests)."""
    if not allow_prompt or os.environ.get("MUCLI_NO_JOBS_TUI"):
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def open_jobs_tui(*, start_job: Optional[str] = None, service: Any = None, creator: Any = None) -> None:
    from .data import JobsData
    from .run import run_jobs_tui
    run_jobs_tui(data=JobsData(service, creator=creator), start_job=start_job)


__all__ = ["can_open_tui", "open_jobs_tui"]
