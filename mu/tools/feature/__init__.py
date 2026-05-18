"""Feature-mode tools (plan engine).

18 `@tool`-decorated handlers covering the full feature-plan lifecycle:
create / phases / tasks / status transitions / reviews / diffs /
approval / blockers. Handler bodies still live in `core/tools.py` (they
delegate to the `core/feature_mode.py` engine); this module pulls them
into the new registry so the agent loop sees them via `mu.tools`.
"""

from . import handlers  # noqa: F401 — registers 18 feature-mode tools at import time

__all__: list = []
