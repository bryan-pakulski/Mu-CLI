"""Session-owned scan pointers and operator execution choices."""
from __future__ import annotations

import copy
from mu.modes.state import ModeState, ModeStates, register_mode_state


@register_mode_state
class SecurityState(ModeState):
    name = "security"

    def __init__(self):
        super().__init__()
        self.load_dict(None)

    def load_dict(self, data):
        data = data or {}
        self.active = str(data.get("active") or "")
        self.reports = copy.deepcopy(data.get("reports") or {})
        self.revision = int(data.get("revision") or 0)
        # Execution consent belongs to the session, never a model-editable report.
        self.execution = copy.deepcopy(data.get("execution") or {})
        self.previews = {}  # transient approval snapshots, never persisted

    def to_dict(self):
        return {"active": self.active, "reports": copy.deepcopy(self.reports),
                "revision": self.revision, "execution": copy.deepcopy(self.execution)}

    def changed(self):
        self.revision += 1
        self.touch()


def security_state(owner):
    # Security consent must never fall back to the process-wide default state.
    for candidate in (owner, getattr(owner, "session_manager", None),
                      getattr(getattr(owner, "session", None), "session_manager", None)):
        states = getattr(candidate, "mode_state", None)
        if isinstance(states, ModeStates):
            return states.get("security", SecurityState)
    raise ValueError("security scans require session-owned mode state")
