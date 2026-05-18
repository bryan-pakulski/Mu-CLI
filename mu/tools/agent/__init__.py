"""Agent-related tools (sub-agent spawn).

Currently exposes only `spawn` as a registered stub — full sub-agent
execution lands in a follow-up. The stub establishes the schema so the
model can already issue the call; the handler returns an explicit
"not implemented" envelope rather than silently failing.
"""

from . import spawn  # noqa: F401 — registers the stub

__all__: list = []
