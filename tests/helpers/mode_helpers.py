"""Shared helpers for mode tests: a scripted provider and a real agent session.

``ScriptedProvider(steps)`` answers each provider call with the next step:
a list of ``(tool_name, args)`` tool calls, or a string (final text). Every
call's system prompt and messages are recorded so tests can inspect the
projected request.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Sequence, Union

from providers.base import LLMProvider, MessagePart, ProviderResponse

Step = Union[str, Sequence[tuple]]


class ScriptedProvider(LLMProvider):
    def __init__(self, steps: Iterable[Step] = (), model_name: str = "scripted"):
        super().__init__(model_name)
        self.model_name = model_name
        self.steps: List[Step] = list(steps)
        self.calls: List[dict] = []
        self._counter = 0

    def get_available_models(self):
        return [self.model_name]

    def push(self, *steps: Step) -> None:
        self.steps.extend(steps)

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.calls.append({"system_prompt": system_prompt or "", "messages": list(messages),
                           "tools": [t.name for t in tools or []]})
        step: Step = self.steps.pop(0) if self.steps else "done"
        if isinstance(step, str):
            part = MessagePart(type="text", text=step)
            return ProviderResponse(text=step, parts=[part], input_tokens=50, output_tokens=2,
                                    total_tokens=52)
        parts = []
        for name, args in step:
            self._counter += 1
            parts.append(MessagePart(type="tool_call", tool_name=name, tool_args=dict(args),
                                     tool_call_id=f"c{self._counter}"))
        return ProviderResponse(text="", parts=parts, input_tokens=50, output_tokens=2,
                                total_tokens=52)

    def upload_file(self, *a, **kw):
        return None

    def effective_context_window(self, model_name=None):
        return 480_000


def agent_session(tmp_path, *, mode: str = "default", steps: Iterable[Step] = (),
                  workspace: Any = None, name: str = "", yolo: bool = True):
    """A real ``Session`` with a scripted provider, attached to ``workspace``
    (default ``tmp_path / 'ws'``), saved under ``name`` when given."""
    from mu.session.session import Session, SessionManager
    from mu.workspace.folder_context import FolderContext

    manager = SessionManager()
    provider = ScriptedProvider(steps)
    session = Session(provider, False, "system", manager)
    session.agentic = True
    ws = workspace or (tmp_path / "ws")
    ws.mkdir(parents=True, exist_ok=True)
    folder_context = FolderContext()
    folder_context.add_folder(str(ws))
    session.folder_context = folder_context
    manager.folder_context = folder_context
    session.variables["agent_mode"] = mode
    session.variables["yolo"] = yolo
    if name:
        manager.current_session_name = name
        manager.save_history(folder_context)
    return session


def tool_ctx(session):
    from mu.tools.descriptors import ToolExecutionContext

    return ToolExecutionContext(folder_context=session.folder_context, variables=session.variables,
                                session=session)


def call(session, tool_name: str, **args) -> dict:
    """Dispatch one tool through the real dispatcher; returns the envelope dict."""
    import json

    from mu.tools._dispatcher import dispatch

    raw = dispatch(tool_name, args, session.folder_context, variables=session.variables,
                   session=session)
    return json.loads(raw)


def request_texts(call_record: dict) -> List[str]:
    """Every text part of a recorded provider call's messages."""
    out = []
    for message in call_record["messages"]:
        for part in getattr(message, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                out.append(text)
    return out


def history_texts(session) -> List[str]:
    out = []
    for message in session.session_manager.history:
        for part in message.get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                out.append(part["text"])
    return out
