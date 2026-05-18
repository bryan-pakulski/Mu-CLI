"""Streaming consumer that bridges provider stream events to a live UI.

`StreamRenderer.consume()` takes a provider's `stream()` iterator, taps each
event for UI side-effects (live text deltas, tool-call notices), and then
hands the events to `provider.drain_stream()` to collapse them into a
`ProviderResponse` for the rest of the agent loop. The contract is:

    response = renderer.consume(provider, provider.stream(...))

The renderer is intentionally provider-agnostic and UI-agnostic: it only
calls duck-typed callbacks. Pass callbacks explicitly, or use
`build_default_renderer(ui)` which probes a UI object for a small set of
optional methods (`stream_assistant_delta`, `stream_assistant_end`,
`stream_thinking_delta`, `stream_tool_call`).

Callbacks are deliberately wrapped in try/except: a UI bug must never break
the model stream.
"""

from typing import Any, Callable, Iterable, Optional

from providers.base import LLMProvider, ProviderResponse, StreamEvent


class StreamRenderer:
    def __init__(
        self,
        *,
        on_text_delta: Optional[Callable[[str], None]] = None,
        on_thinking_delta: Optional[Callable[[str], None]] = None,
        on_tool_call_start: Optional[Callable[[str], None]] = None,
        on_done: Optional[Callable[[], None]] = None,
    ):
        self._on_text = on_text_delta
        self._on_thinking = on_thinking_delta
        self._on_tool_start = on_tool_call_start
        self._on_done = on_done

    def consume(
        self,
        provider: LLMProvider,
        events: Iterable[StreamEvent],
    ) -> ProviderResponse:
        def tap() -> Iterable[StreamEvent]:
            try:
                for ev in events:
                    self._notify(ev)
                    yield ev
            finally:
                if self._on_done is not None:
                    try:
                        self._on_done()
                    except Exception:
                        pass

        return provider.drain_stream(tap())

    def _notify(self, ev: StreamEvent) -> None:
        try:
            if ev.kind == "text_delta" and ev.text and self._on_text is not None:
                self._on_text(ev.text)
            elif ev.kind == "thinking_delta" and ev.text and self._on_thinking is not None:
                self._on_thinking(ev.text)
            elif (
                ev.kind == "tool_call_start"
                and ev.tool_name
                and self._on_tool_start is not None
            ):
                self._on_tool_start(ev.tool_name)
        except Exception:
            # Callbacks must never break the stream.
            pass


def build_default_renderer(ui: Any) -> StreamRenderer:
    """Return a StreamRenderer wired to a duck-typed UI.

    Looks for these optional methods on `ui`:
      * stream_assistant_delta(text)   - call for each text delta
      * stream_thinking_delta(text)    - call for each reasoning delta
      * stream_tool_call(tool_name)    - call when a tool call starts
      * stream_assistant_end()         - call once at end of stream

    Missing methods are silently skipped — the renderer then buffers
    silently and only the final ProviderResponse is observable.
    """

    on_text = getattr(ui, "stream_assistant_delta", None)
    on_thinking = getattr(ui, "stream_thinking_delta", None)
    on_tool = getattr(ui, "stream_tool_call", None)
    on_done = getattr(ui, "stream_assistant_end", None)

    return StreamRenderer(
        on_text_delta=on_text if callable(on_text) else None,
        on_thinking_delta=on_thinking if callable(on_thinking) else None,
        on_tool_call_start=on_tool if callable(on_tool) else None,
        on_done=on_done if callable(on_done) else None,
    )
