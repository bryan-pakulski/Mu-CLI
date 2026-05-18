"""Skill invocation tool (`invoke_skill`).

Reads from `mu/skills/` and the workspace-local `.mu/skills/` directories
to load the full body of a declared skill into context.
"""

from . import handlers  # noqa: F401 — registers invoke_skill at import time

__all__: list = []
