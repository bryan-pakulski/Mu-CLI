"""Edit tracker: which files the agent changed and when (modes v2 §4.1, §4.3).

A post-tool hook records every successful write-side edit (``write_file``,
``apply_diff``, ``search_and_replace_file``) with the turn it happened in.
Gates that need "a passing check after the last edit" (feature review and
completion, the default-mode turn receipt) read it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .state import ModeState, now, register_mode_state, state_for

EDIT_TOOLS = frozenset({"write_file", "apply_diff", "search_and_replace_file"})
MAX_EDITS = 300


@register_mode_state
class EditsState(ModeState):
    name = "edits"

    def __init__(self) -> None:
        super().__init__()
        self.edits: List[Dict[str, Any]] = []
        self.last_edit_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"edits": self.edits[-MAX_EDITS:], "last_edit_at": self.last_edit_at}

    def load_dict(self, data: Optional[Dict[str, Any]]) -> None:
        data = data or {}
        self.edits = [e for e in data.get("edits") or [] if isinstance(e, dict)][-MAX_EDITS:]
        self.last_edit_at = float(data.get("last_edit_at") or 0.0)

    def record(self, path: str, tool: str, turn: Any = None) -> None:
        with self.lock:
            at = now()
            self.edits.append({"path": str(path), "tool": tool, "at": at, "turn": turn})
            self.edits = self.edits[-MAX_EDITS:]
            self.last_edit_at = at
            self.touch()

    def since(self, at: float) -> List[Dict[str, Any]]:
        return [e for e in self.edits if float(e.get("at") or 0) >= at]

    def in_turn(self, turn: Any) -> List[Dict[str, Any]]:
        return [e for e in self.edits if e.get("turn") == turn]


def edits_state(owner: Any) -> EditsState:
    return state_for(owner, "edits")


def _succeeded(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("ok", True)) and not result.get("error_code")
    text = str(result or "")
    if text.lstrip().startswith("{"):
        import json

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return bool(data.get("ok", True)) and not data.get("error_code")
        except ValueError:
            pass
    return not text.lstrip().lower().startswith("error")


def _install_hook() -> None:
    from mu.agent.hooks import HookSpec, default_registry

    def _hook(ctx):
        if (ctx.tool_name or "") not in EDIT_TOOLS or ctx.session is None:
            return None
        if not _succeeded(ctx.tool_result):
            return None
        states = getattr(getattr(ctx.session, "session_manager", None), "mode_state", None)
        if states is None:
            return None
        args = ctx.tool_args or {}
        path = str(args.get("filename") or args.get("file") or args.get("path") or "")
        states.get("edits").record(path, ctx.tool_name, getattr(ctx.session, "_current_turn_start_index", None))
        return None

    default_registry.remove("mode_edit_tracker")
    default_registry.add(HookSpec(name="mode_edit_tracker", point="post_tool", priority=50, handler=_hook))


_install_hook()


__all__ = ["EDIT_TOOLS", "EditsState", "edits_state"]
