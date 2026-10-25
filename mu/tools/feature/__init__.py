"""Feature-mode tools over the feature plan engine (``mu/feature/engine.py``).

The agent proposes a draft, executes one task at a time with evidence-backed
exit criteria, reviews and requests completion; plan approval, diff decisions
and completion belong to the user and have no tools.
"""

from . import handlers  # noqa: F401 — registers the feature plan tools

__all__: list = []
