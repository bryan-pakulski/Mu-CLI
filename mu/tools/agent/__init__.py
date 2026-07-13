"""Agent-related tools — async sub-agent orchestrator.

Exposes `spawn_agent` (dispatch a child to a background thread, return a
task_id), `poll_subagent` (non-blocking status snapshot), and
`kill_subagent` (cooperative cancel with partial-result capture).
"""

from . import spawn  # noqa: F401 — registers spawn_agent
from . import poll  # noqa: F401 — registers poll_subagent
from . import kill  # noqa: F401 — registers kill_subagent

__all__: list = []