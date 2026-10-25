"""Shared building blocks for agent modes (modes v2 §3).

``state``    session-owned typed mode state, bound per tool call
``evidence`` ``CheckRun`` / ``run_check``: engine-run commands with predicates
``work``     work items (the plan), one in progress, evidence on done
``blockers`` session blockers raised by the agent, resolved by the user
``events``   actors and override events for gated transitions
``capsule``  request-only mode state for the turn context
``preview``  approval previews for tools that run stored commands
``receipt``  default mode's turn receipt
"""

from .state import (
    ModeState,
    ModeStates,
    current_mode_states,
    load_cached,
    register_mode_state,
    state_for,
    states_of,
    use_mode_states,
)

__all__ = [
    "ModeState",
    "ModeStates",
    "current_mode_states",
    "load_cached",
    "register_mode_state",
    "state_for",
    "states_of",
    "use_mode_states",
]
