from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import select
import sys
import threading
import time

from utils.threads import NamedThread
from rich import box
from rich.align import Align
from rich.console import Group
from rich.errors import MarkupError
from rich.protocol import is_renderable as _is_rich_renderable
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .input import InputHandler
from .render import render_response
from .theme import components, get_console, glyphs
from .theme.pygments import syntax_theme
from utils.config import AGENT_MODE_METADATA
from utils.helpers import safe_markup
from utils.runtime_metrics import build_live_status_line, collect_runtime_metrics


class RichUI:
    def __init__(self):
        self.console = get_console()
        self.input_handler = InputHandler()
        # Per-turn streaming state. `_gen_live` is set when an active
        # `_GenerationLive` context manager is open — streaming-text and
        # thinking deltas append into that Live's buffer so the Rich Live
        # region (not bare `console.print`) handles cursor placement.
        # `_streamed_any_text` survives until the next stream starts so
        # `render_message` can suppress the duplicate panel.
        self._gen_live = None
        self._streamed_any_text = False
        self._variables = None  # set via set_variables()
        # Tool markers printed after a generation (``● kind target``) that
        # are waiting for their result, oldest first. Each entry is a dict
        # with name, target, started and the console print count after the
        # marker was printed (for in-place rewriting).
        self._pending_tools: list = []
        self._tool_clock = None

    # -------------------------------------------------- live streaming surface

    def _streaming_enabled(self) -> bool:
        """Honor `variables["streaming_enabled"]` (default True)."""
        if self._variables is None:
            return True
        try:
            return bool(self._variables.get("streaming_enabled", True))
        except AttributeError:
            return True

    def stream_assistant_start(self, model_name=None):
        """No-op: header rendering is owned by the active `_GenerationLive`.
        Kept for API compatibility."""
        return None

    def stream_assistant_delta(self, text: str):
        """Append a token chunk to the active generation Live, if any."""
        if not self._streaming_enabled() or not text:
            return
        if self._gen_live is not None:
            self._gen_live.append_text(text)
            self._streamed_any_text = True
            return
        # No active Live — happens in tests or non-streaming paths. Fall
        # back to a direct print so the text isn't lost.
        self.console.print(text, end="", soft_wrap=True, highlight=False, markup=False)
        self._streamed_any_text = True

    def stream_thinking_delta(self, text: str):
        """Append a reasoning chunk to the active generation Live, styled
        ``wash.thinking`` (muted italic) so it separates from user-facing text.

        Gated on `show_thinking` (default True), with one mode-aware
        wrinkle: teacher mode hides thinking by default unless the user
        has explicitly toggled `/show-thinking on` (tracked via
        `show_thinking_explicit`). The model still GENERATES reasoning
        when `session.thinking=True` — this gate only controls rendering.
        """
        if not self._streaming_enabled() or not text:
            return
        if not self._show_thinking_effective():
            return
        if self._gen_live is not None:
            self._gen_live.append_thinking(text)
            return
        self.console.print(
            Text(text, style="wash.thinking"), end="", soft_wrap=True, highlight=False
        )

    def _show_thinking_effective(self) -> bool:
        """Resolve `show_thinking` against per-mode defaults.

        Teacher mode hides thinking by default — the lecture cadence
        works better without dim-italic reasoning blocks competing with
        agent_explanation turns for the learner's attention. Any other
        mode falls back to the stored `show_thinking` value.

        The user's `/show-thinking` toggle wins: once they set the
        explicit flag, mode-based overrides stop applying.
        """
        variables = self._variables or {}
        explicit = bool(variables.get("show_thinking_explicit", False))
        stored = bool(variables.get("show_thinking", True))
        if explicit:
            return stored
        mode = str(variables.get("agent_mode", "default") or "default").lower()
        if mode == "teacher":
            return False
        return stored

    def stream_tool_call(self, tool_name: str):
        """Note a tool call inside the generation Live's text region.
        Outside an active Live, fall back to a one-line print."""
        if not self._streaming_enabled() or not tool_name:
            return
        if self._gen_live is not None:
            self._gen_live.note_tool_call(tool_name)
            return
        self.console.print(components.tool_marker(str(tool_name)), highlight=False)

    def stream_assistant_end(self):
        """End-of-stream notification. The Live keeps its rendered text in
        scrollback (transient=False) when its CM exits, so we don't have to
        print anything here. `_streamed_any_text` is deliberately preserved
        so `render_message` can suppress the duplicate panel; it resets on
        the next Live start."""
        return None

    # -------------------------------------------------- render_message

    def render_message(self, role, content, model_name=None):
        local_now = datetime.now().astimezone()
        ts = local_now.strftime(f"%H:%M:%S {local_now.tzname() or 'local'}")
        if role == "user":
            # G§9.7 T: a `›` gutter mark in accent, then the plain text.
            body = Text()
            body.append(f"{glyphs.USER_GUTTER} ", style="wash.accent.bold")
            body.append(str(content), style="wash.ink")
            self.console.print(body)
            return
        # Assistant path. If the text already streamed token-by-token, the
        # user has seen it — `_GenerationLive.__exit__` already re-emitted
        # it via `render_response`. Don't redraw the panel (duplicate).
        # The `_streamed_any_text` flag survives until the next stream's
        # `__enter__` clears it. History replay and other non-streaming
        # callers never set the flag, so they always render normally.
        if self._streamed_any_text:
            return
        header = Text()
        header.append("\nAssistant", style="wash.ink2.bold")
        if model_name:
            header.append(f" ({model_name})", style="wash.muted")
        header.append(f" [{ts}]:", style="wash.muted")
        self.console.print(header)
        render_response(content)

    def render_visualization(self, artifact, *, local_path=None):
        """Render a compact TUI wrapper with an OSC-8 browser link."""
        descriptor = artifact if isinstance(artifact, dict) else {}
        title = str(descriptor.get("title") or descriptor.get("name") or "Visualization")
        url = str(descriptor.get("view_url") or "")
        if local_path:
            try:
                url = Path(local_path).resolve().as_uri()
            except (OSError, ValueError):
                pass

        body = Text()
        body.append("Interactive visualization ready.\n", style="wash.muted")
        if url:
            body.append("Open visualization", style="wash.link")
            body.stylize(f"link {url}", len(body) - len("Open visualization"))
            body.append(f"\n{url}", style="wash.muted")
        else:
            body.append("Open it from the web or mobile Artifacts view.", style="wash.muted")
        self.console.print(components.panel(body, title=Text(title, style="wash.heading")))

    def get_input(
        self,
        session_name,
        staged_files,
        agent_mode="default",
        current_task=None,
        feature_context=None,
    ):
        return self.input_handler.get_input(
            session_name,
            staged_files,
            agent_mode=agent_mode,
            current_task=current_task,
            feature_context=feature_context,
        )

    def set_variables(self, variables_dict):
        self._variables = variables_dict
        self.input_handler.set_variables(variables_dict)

    def set_provider_source(self, source):
        self.input_handler.set_provider_source(source)

    def confirm(self, message, default=True):
        return Confirm.ask(message, default=default)

    def prompt_choices(self, message, choices, default=None):
        return Prompt.ask(message, choices=choices, default=default)

    def prompt(self, message, default=None):
        return Prompt.ask(message, default=default)

    def request_tool_approval(
        self,
        *,
        tool_name=None,
        tool_args=None,
        display_args=None,
        count_info="",
        can_approve=True,
        modifications=None,
        preview_error=None,
        error_code=None,
        approval_policy="default",
        prompt_text,
        choices,
        default,
    ):
        self.console.print(
            self.build_approval_panel(
                tool_name=tool_name,
                display_args=display_args if display_args is not None else tool_args,
                count_info=count_info,
                can_approve=can_approve,
                preview_error=preview_error,
                prompt_text=prompt_text,
                choices=choices,
                approval_policy=approval_policy,
            )
        )
        choice = self.input_handler.prompt_choice(
            "Approval choice",
            choices=choices,
            default=default,
        )
        reason = None
        if choice == "e":
            reason = self.prompt("Provide an explanation to the model")
        return choice, reason

    _APPROVAL_KEY_LABELS = {
        "y": "approve",
        "a": "always",
        "n": "deny",
        "e": "explain",
    }

    def build_approval_panel(
        self,
        *,
        tool_name=None,
        display_args=None,
        count_info="",
        can_approve=True,
        preview_error=None,
        prompt_text="",
        choices=("y", "n", "e"),
        approval_policy="default",
    ):
        """The approval card (G§9.9 T): attention border, ``◆ Needs you``
        title, escaped args, and a key-hint row. All model-supplied text is
        added as plain ``Text`` so it can never be parsed as markup."""
        name = str(tool_name or "").strip()
        if not name and prompt_text:
            # Callers that only pass the legacy prompt text still get a card.
            from mu.ui.markup import to_plain

            body = Text(to_plain(prompt_text).strip(), style="wash.ink")
        else:
            body = Text()
            body.append("Tool  ", style="wash.muted")
            body.append(name or "tool", style="wash.ink.bold")
            if count_info:
                body.append(f"  {count_info}".rstrip(), style="wash.muted")
            if display_args:
                body.append("\nArgs  ", style="wash.muted")
                body.append(str(display_args), style="wash.ink2")
            if preview_error:
                body.append("\n")
                body.append(f"{glyphs.FAILED} {preview_error}", style="wash.danger")
        hints = components.key_hints(
            (key, self._APPROVAL_KEY_LABELS.get(key, key)) for key in choices
        )
        parts = [body, Text(""), hints]
        if approval_policy != "always_human":
            parts.append(
                Text(
                    "Shift+Tab toggles YOLO for this and later approvals in the current loop.",
                    style="wash.hint",
                )
            )
        if can_approve:
            # Panel titles must not carry a base style: Rich lets it override spans.
            title = Text()
            title.append(f"{glyphs.WAITING} Needs you", style="wash.attention.bold")
            title.append(" · ", style="wash.muted")
            title.append(f"Permission to run {name or 'tool'}", style="wash.ink.bold")
            tone = "attention"
        else:
            title = Text()
            title.append(f"{glyphs.FAILED} Diff failed", style="wash.danger.bold")
            title.append(" · ", style="wash.muted")
            title.append(f"{name or 'tool'} can't be previewed", style="wash.ink.bold")
            tone = "danger"
        return components.panel(Group(*parts), title=title, tone=tone, padding=(0, 1))

    def show_error(self, message):
        if isinstance(message, Text):
            self.console.print(message, style="wash.danger")
        else:
            self.console.print(Text(str(message), style="wash.danger"))

    def show_info(self, message):
        if not isinstance(message, (str, Text)) and _is_rich_renderable(message):
            self.show_renderable(message)
            return
        if isinstance(message, Text):
            text = message.plain
        else:
            text = str(message)
        if "Running tool:" in text.lstrip()[:20]:
            self._note_running_tool(text)
        if self._is_silenced_in_compact_mode(text):
            return
        if isinstance(message, Text):
            self.console.print(message, style="wash.ink2")
        else:
            self.console.print(Text(text, style="wash.ink2"))

    def show_info_markup(self, message):
        """Print a message whose Rich markup was written by our own code.

        Interpolated values must already be escaped with ``mu.ui.markup.esc``.
        Malformed markup is printed literally instead of raising.
        """
        try:
            text = Text.from_markup(str(message))
        except MarkupError:
            text = Text(str(message))
        if self._is_silenced_in_compact_mode(text.plain):
            return
        self.console.print(text)

    def show_renderable(self, renderable):
        """Print a Rich renderable (Table, Panel, ...) as-is."""
        self.console.print(renderable)

    def show_notice(self, kind, message):
        """Mode banner / toggle: ``glyph message`` in the semantic colour.

        ``message`` is printed literally (never parsed as markup)."""
        self.console.print(components.notice_text(kind, str(message)))

    def _is_silenced_in_compact_mode(self, text: str) -> bool:
        """Filter noisy diagnostic messages when verbose rendering is off.

        Compact mode (the default) hides per-turn token lines, the
        post-iteration final-totals line, the compaction notice, the
        full tool-args echo, and the collated-tool indicator. The
        compact streaming `→ tool_name` indicator stays — it comes
        from `stream_tool_call`, not from `show_info`.
        """
        variables = self._variables or {}
        if variables.get("verbose", False):
            return False
        stripped = text.lstrip()
        if "Running tool:" in stripped[:20]:
            return True
        if stripped.startswith("Tokens:"):
            return True
        if stripped.startswith("Final session tokens:"):
            return True
        if "Compacting turn history" in stripped:
            return True
        if stripped.startswith("[Collated:"):
            return True
        return False

    def build_meter(
        self,
        label,
        current,
        maximum,
        *,
        color="wash.meter.fill",
        width=16,
        warning_threshold=components.CAUTION_AT,
        danger_threshold=components.DANGER_AT,
    ):
        """``LABEL ██████░░░░ 12/40``: ink-2 fill, caution at ≥0.65, danger at ≥0.85."""
        return components.meter(
            label,
            current,
            maximum,
            width=width,
            fill_style=color,
            caution_at=warning_threshold,
            danger_at=danger_threshold,
        )

    def build_memory_monitor(self, session):
        from mu.design import load_tokens

        metrics = collect_runtime_metrics(session)

        meters = [
            self.build_meter("CTX", metrics["ctx"]["current"], metrics["ctx"]["maximum"]),
            self.build_meter("MEM", metrics["mem"]["current"], metrics["mem"]["maximum"]),
            self.build_meter(
                "SCRATCH", metrics["scratch"]["current"], metrics["scratch"]["maximum"]
            ),
            self.build_meter("QUEUE", metrics["queue"]["current"], metrics["queue"]["maximum"]),
        ]

        current_mode = metrics["mode"]["name"]
        mode_description = AGENT_MODE_METADATA.get(current_mode, {}).get(
            "description", ""
        )
        mode_style = (
            f"wash.mode.{current_mode}"
            if current_mode in load_tokens()["mode"]
            else "wash.ink.bold"
        )

        def sep(text):
            text.append("  ·  ", style="wash.muted")

        meta = Text()
        meta.append("tokens ", style="wash.muted")
        meta.append(str(metrics["tokens"]["total"]), style="wash.ink.bold")
        sep(meta)
        meta.append("queue ", style="wash.muted")
        meta.append(str(metrics["queue_items"]), style="wash.ink.bold")
        meta.append(" item", style="wash.muted")
        if metrics["queue_items"] != 1:
            meta.append("s", style="wash.muted")
        sep(meta)
        meta.append("mode ", style="wash.muted")
        meta.append(current_mode, style=mode_style)

        token_line = Text()
        token_line.append("in ", style="wash.muted")
        token_line.append(str(metrics["tokens"]["input"]), style="wash.ink.bold")
        token_line.append("  out ", style="wash.muted")
        token_line.append(str(metrics["tokens"]["output"]), style="wash.ink.bold")
        token_line.append("  cost ", style="wash.muted")
        token_line.append(f"${metrics['tokens']['total_cost']:.5f}", style="wash.ink.bold")

        mode_line = Text()
        mode_line.append("mode info ", style="wash.muted")
        mode_line.append(mode_description or "No description available.", style="wash.ink2")

        feature_group = []
        feature_metrics = metrics.get("feature")
        if feature_metrics:
            feature_state = feature_metrics.get("state") or {}
            feature_plan = feature_metrics.get("plan") or {}
            feature_name = feature_plan.get(
                "feature_name", feature_state.get("directory", "Unknown feature")
            )
            feature_status = feature_state.get("status", "unknown")
            feature_group.append(Text(""))

            feature_header = Text()
            feature_header.append("feature ", style="wash.muted")
            feature_header.append(str(feature_name), style="wash.ink.bold")
            feature_group.append(feature_header)

            feature_meta = Text()
            feature_meta.append("loop ", style="wash.muted")
            feature_meta.append(str(feature_status), style="wash.running")
            if feature_plan:
                sep(feature_meta)
                feature_meta.append("review ", style="wash.muted")
                feature_meta.append(
                    str(feature_plan.get("review_status", "unknown")), style="wash.ink2"
                )
            feature_group.append(feature_meta)

            phases = feature_plan.get("phases", [])
            progress = feature_metrics.get("progress") or {}
            completed_phases = sum(
                1 for phase in phases if phase.get("status") == "completed"
            )
            phase_count = max(1, int(feature_plan.get("phase_count", len(phases)) or 1))
            feature_group.append(
                self.build_meter(
                    "PHASES",
                    completed_phases,
                    phase_count,
                    # Progress meters fill up on purpose: no pressure colours.
                    warning_threshold=2.0,
                    danger_threshold=2.0,
                )
            )

            for phase in phases[:4]:
                counts = phase.get("task_counts", {})
                total_tasks = max(1, sum(int(value or 0) for value in counts.values()))
                completed_tasks = int(counts.get("completed", 0) or 0)
                phase_color = {
                    "completed": "wash.ok",
                    "in_progress": "wash.running",
                }.get(phase.get("status"), "wash.meter.fill")
                feature_group.append(
                    self.build_meter(
                        f"P{phase.get('number', '?')}",
                        completed_tasks,
                        total_tasks,
                        color=phase_color,
                        width=12,
                        warning_threshold=2.0,
                        danger_threshold=2.0,
                    )
                )

            if progress:
                next_phase = progress.get("next_phase") or {}
                active_label = next_phase.get("title") or "Review"
                elapsed_seconds = int(progress.get("elapsed_seconds", 0) or 0)
                elapsed = self._format_elapsed(elapsed_seconds)
                token_delta = int(progress.get("token_delta", 0) or 0)
                activity = Text()
                activity.append(f"{glyphs.ACTIVE} ", style="wash.running")
                activity.append(f"Implementing {active_label}… ", style="wash.ink")
                activity.append(
                    f"({elapsed} · ↓ {self._format_token_delta(token_delta)} tokens)",
                    style="wash.muted",
                )
                feature_group.append(activity)

                completed = int(progress.get("completed_tasks", 0) or 0)
                remaining = max(0, int(progress.get("total_tasks", 0) or 0) - completed)
                summary_line = Text()
                summary_line.append(f"{glyphs.DONE} ", style="wash.ok")
                summary_line.append(f"{completed} completed", style="wash.ink")
                summary_line.append(f"  {glyphs.PENDING} ", style="wash.muted")
                summary_line.append(f"{remaining} remaining", style="wash.ink")
                feature_group.append(summary_line)

            max_visible_tasks = 10
            visible_tasks = phases[:max_visible_tasks]
            hidden_tasks = phases[max_visible_tasks:]
            hidden_completed = sum(
                1 for task in hidden_tasks if task.get("status") == "completed"
            )
            for phase in visible_tasks:
                status = {
                    "completed": "done",
                    "in_progress": "active",
                    "not_started": "pending",
                }.get(phase.get("status"), "pending")
                task_line = Text()
                task_line.append(f"{glyphs.status_glyph(status)} ", style=f"wash.status.{status}")
                task_line.append(str(phase.get("title", "")), style="wash.ink")
                feature_group.append(task_line)
            if hidden_completed > 0:
                hidden_line = Text()
                hidden_line.append("… ", style="wash.muted")
                hidden_line.append(f"+{hidden_completed} completed", style="wash.muted")
                feature_group.append(hidden_line)

            next_phase = feature_plan.get("next_phase")
            if next_phase:
                next_phase_line = Text()
                next_phase_line.append("next ", style="wash.muted")
                next_phase_line.append(
                    f"P{next_phase.get('number')}: {next_phase.get('title', '')}",
                    style="wash.ink",
                )
                feature_group.append(next_phase_line)

        legend = Text(
            "CTX context · MEM memory · SCRATCH scratchpad · QUEUE collation",
            style="wash.muted",
        )

        return Align.center(
            components.panel(
                Group(
                    *meters,
                    Text(""),
                    meta,
                    token_line,
                    mode_line,
                    *feature_group,
                    legend,
                ),
                title=Text("/stats", style="wash.heading"),
                width=76,
            )
        )

    def show_memory_monitor(self, session):
        self.console.print(self.build_memory_monitor(session))

    @staticmethod
    def _format_elapsed(total_seconds):
        total_seconds = max(0, int(total_seconds or 0))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m {seconds}s"
        return f"{minutes}m {seconds}s"

    @staticmethod
    def _format_token_delta(tokens):
        tokens = max(0, int(tokens or 0))
        if tokens >= 1000:
            return f"{tokens / 1000:.1f}k"
        return str(tokens)

    #: side-by-side diff only on wide terminals (spec §6.3)
    SIDE_BY_SIDE_MIN_WIDTH = 160

    def show_diff(self, filename, original_content, new_content, *, side_by_side=None):
        """Show a proposed change: unified by default, side-by-side above 160 columns.

        Added/removed lines use ok/danger foregrounds; a background tint from
        the detected theme's washes is added only in truecolor (the
        ``wash.diff.*.line`` styles resolve that per colour mode).
        """
        self.console.print(
            self.build_diff(filename, original_content, new_content, side_by_side=side_by_side)
        )

    def build_diff(self, filename, original_content, new_content, *, side_by_side=None):
        import difflib
        import os

        ext = os.path.splitext(str(filename))[1][1:] or "txt"
        orig_lines = str(original_content or "").splitlines()
        new_lines = str(new_content or "").splitlines()
        sm = difflib.SequenceMatcher(None, orig_lines, new_lines)
        grouped_opcodes = list(sm.get_grouped_opcodes(n=3))

        if side_by_side is None:
            try:
                width = int(self.console.width)
            except (TypeError, ValueError, AttributeError):
                width = 0
            side_by_side = width >= self.SIDE_BY_SIDE_MIN_WIDTH

        additions = sum(
            (j2 - j1) for group in grouped_opcodes for tag, _i1, _i2, j1, j2 in group
            if tag in ("insert", "replace")
        )
        deletions = sum(
            (i2 - i1) for group in grouped_opcodes for tag, i1, i2, _j1, _j2 in group
            if tag in ("delete", "replace")
        )
        title = Text()
        title.append("Proposed changes ", style="wash.heading")
        title.append(str(filename), style="wash.path")
        title.append(f"  +{additions}", style="wash.diff.add")
        title.append(f" -{deletions}", style="wash.diff.del")

        if side_by_side:
            body = self._side_by_side_diff(grouped_opcodes, orig_lines, new_lines, ext)
        else:
            body = self._unified_diff(grouped_opcodes, orig_lines, new_lines, ext)
        return components.panel(body, title=title, padding=(0, 1))

    def _code(self, line, ext):
        return Syntax(line, ext, theme=syntax_theme(), background_color="default", word_wrap=True)

    def _unified_diff(self, grouped_opcodes, orig_lines, new_lines, ext):
        table = Table(
            box=None, show_header=False, expand=True, pad_edge=False, padding=(0, 1)
        )
        table.add_column("old", justify="right", style="wash.muted", width=5, no_wrap=True)
        table.add_column("new", justify="right", style="wash.muted", width=5, no_wrap=True)
        table.add_column("line", ratio=1)
        for group in grouped_opcodes:
            first = group[0]
            table.add_row(
                "", "",
                Text(f"@@ -{first[1] + 1} +{first[3] + 1} @@", style="wash.diff.hunk"),
            )
            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    for k in range(i2 - i1):
                        table.add_row(
                            str(i1 + k + 1), str(j1 + k + 1),
                            self._code("  " + orig_lines[i1 + k], ext),
                        )
                    continue
                if tag in ("delete", "replace"):
                    for k in range(i2 - i1):
                        table.add_row(
                            Text(str(i1 + k + 1), style="wash.diff.del"), "",
                            Text("- " + orig_lines[i1 + k], style="wash.diff.del.line"),
                        )
                if tag in ("insert", "replace"):
                    for k in range(j2 - j1):
                        table.add_row(
                            "", Text(str(j1 + k + 1), style="wash.diff.add"),
                            Text("+ " + new_lines[j1 + k], style="wash.diff.add.line"),
                        )
        return table

    def _side_by_side_diff(self, grouped_opcodes, orig_lines, new_lines, ext):
        table = Table(
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="wash.table.header",
            border_style="wash.border",
            expand=True,
            show_edge=False,
            pad_edge=False,
            collapse_padding=True,
        )
        table.add_column("L#", justify="right", style="wash.muted", width=5)
        table.add_column("Current", ratio=1)
        table.add_column("R#", justify="right", style="wash.muted", width=5)
        table.add_column("Proposed", ratio=1)
        for group in grouped_opcodes:
            table.add_row(
                Text("…", style="wash.diff.hunk"),
                Text(f"@@ -{group[0][1] + 1} +{group[0][3] + 1} @@", style="wash.diff.hunk"),
                Text("…", style="wash.diff.hunk"),
                Text(""),
            )
            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    for k in range(i2 - i1):
                        table.add_row(
                            str(i1 + k + 1), self._code(orig_lines[i1 + k], ext),
                            str(j1 + k + 1), self._code(new_lines[j1 + k], ext),
                        )
                    continue
                span = max(i2 - i1, j2 - j1)
                for k in range(span):
                    l_idx, r_idx = i1 + k, j1 + k
                    has_l = tag in ("delete", "replace") and l_idx < i2
                    has_r = tag in ("insert", "replace") and r_idx < j2
                    table.add_row(
                        Text(str(l_idx + 1), style="wash.diff.del") if has_l else "",
                        Text("- " + orig_lines[l_idx], style="wash.diff.del.line") if has_l else "",
                        Text(str(r_idx + 1), style="wash.diff.add") if has_r else "",
                        Text("+ " + new_lines[r_idx], style="wash.diff.add.line") if has_r else "",
                    )
        return table

    def show_status(self, message):
        """Open a unified streaming + status `_GenerationLive`.

        Replaces the old `console.status` spinner. The returned context
        manager opens a single Rich `Live` containing:
          * top: the accumulating assistant text (token-streamed)
          * middle: any accumulating thinking content (dim italic)
          * bottom: a spinner + the status message

        Streaming deltas pumped through `stream_assistant_delta` /
        `stream_thinking_delta` / `stream_tool_call` write into the same
        Live so token output never fights with the spinner for cursor
        position. The status line stays anchored at the bottom.

        Returned object supports `update(new_message)` so callers that
        previously used the rich Status API keep working.
        """
        return _GenerationLive(self, message)

    def build_live_status(self, session, model_name, iteration, max_iterations):
        metrics = collect_runtime_metrics(session)
        status = Text()
        status.append(f"Generating ({model_name}) it {iteration}/{max_iterations} | ")
        if metrics["yolo"]["enabled"]:
            # YOLO is caution (sun), bold, never blinking.
            status.append("YOLO:on", style="wash.caution.bold")
            status.append(" | ", style="wash.muted")
        else:
            status.append("YOLO:off | ", style="wash.muted")
        status.append(build_live_status_line(session), style="wash.ink2")
        return status

    @staticmethod
    def _status_with_yolo(base_message, enabled):
        suffix = "YOLO:on" if enabled else "YOLO:off"
        raw = base_message.plain if isinstance(base_message, Text) else str(base_message)
        raw = raw.replace("YOLO:off", suffix)
        raw = raw.replace("YOLO:on", suffix)
        return raw

    def _start_yolo_status_watcher(self, *, status, base_message, stop_event):
        fd = None
        old_attrs = None
        try:
            if not sys.stdin.isatty():
                return None
            fd = sys.stdin.fileno()
            import termios
            import tty

            old_attrs = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:
            return None

        def _watch():
            buffer = ""
            try:
                while not stop_event.is_set():
                    ready, _, _ = select.select([fd], [], [], 0.1)
                    if not ready:
                        continue
                    chunk = os.read(fd, 32).decode(errors="ignore")
                    if not chunk:
                        continue
                    combined = buffer + chunk
                    while "\x1b[Z" in combined:
                        combined = combined.replace("\x1b[Z", "", 1)
                        enabled = self.input_handler.toggle_yolo_mode()
                        status.update(self._status_with_yolo(base_message, enabled))
                    buffer = combined[-6:]
                    time.sleep(0.02)
            finally:
                if old_attrs is not None and fd is not None:
                    try:
                        import termios

                        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
                    except Exception:
                        pass

        watcher = NamedThread(target=_watch, daemon=True, name="rich-ui-watcher")
        watcher.start()
        return watcher

    # ------------------------------------------------------ tool markers

    def _now(self):
        return (self._tool_clock or time.monotonic)()

    def _note_running_tool(self, text):
        """Attach a target (path, command, query) to the oldest pending marker
        for this tool, from the ``Running tool: name(args)`` notice."""
        import re

        match = re.search(r"Running tool:\s*([\w.\-]+)\s*(?:\((.*)\))?\s*$", str(text), re.S)
        if not match:
            return
        name, args = match.group(1), match.group(2) or ""
        target = ""
        found = re.search(
            r"""['"](?:filename|file_path|path|command|cmd|query|pattern|url|directory|dir)['"]\s*:\s*['"]([^'"]*)""",
            args,
        )
        if found:
            target = found.group(1)
        entry = next(
            (e for e in self._pending_tools if e["name"] == name and not e.get("noted")),
            None,
        )
        if entry is None:
            # No marker was printed (e.g. a non-streaming provider): track it
            # so the result still prints a completed marker.
            entry = {"name": name, "target": "", "printed_at": None}
            self._pending_tools.append(entry)
        entry["noted"] = True
        if target:
            entry["target"] = target[:80]
        entry["started"] = self._now()

    def _emit_tool_markers(self, names):
        """Print ``● kind  name`` for each tool the model just called."""
        for name in names:
            name = str(name or "")
            if not name:
                continue
            self.console.print(components.tool_marker(name), highlight=False)
            printed_at = getattr(self.console, "print_count", None)
            entry = next(
                (
                    e
                    for e in self._pending_tools
                    if e["name"] == name and e.get("printed_at") is None
                ),
                None,
            )
            if entry is not None:
                entry["printed_at"] = printed_at
                continue
            self._pending_tools.append(
                {
                    "name": name,
                    "target": "",
                    "started": self._now(),
                    "printed_at": printed_at,
                }
            )

    def _complete_tool_marker(self, ok):
        """Rewrite the oldest pending ``●`` marker as ``✓ kind target 0.4s``.

        The line is rewritten in place only when the console is a terminal
        and nothing else has been printed since the markers; otherwise the
        completed marker is printed as a new line.
        """
        if not self._pending_tools:
            return
        entry = self._pending_tools.pop(0)
        now = self._now()
        elapsed = max(0.0, now - float(entry.get("started") or now))
        line = components.tool_marker(
            entry["name"],
            entry.get("target") or "",
            state="done" if ok else "failed",
            elapsed=elapsed,
        )
        # The next tool of a sequential batch is timed from here.
        if self._pending_tools and not self._pending_tools[0].get("noted"):
            self._pending_tools[0]["started"] = now
        console = self.console
        count = getattr(console, "print_count", None)
        printed_at = entry.get("printed_at")
        later = [e for e in self._pending_tools if e.get("printed_at") is not None]
        last_marker = max([printed_at or 0] + [e["printed_at"] for e in later])
        in_place = (
            isinstance(count, int)
            and printed_at is not None
            and count == last_marker
            and getattr(console, "is_terminal", False) is True
        )
        if in_place:
            up = 1 + len(later)
            try:
                console.file.write(f"\x1b[{up}F\x1b[2K")
                console.print(line, highlight=False, end="")
                console.file.write(f"\x1b[{up}E")
                console.file.flush()
                # Rewriting does not move the logical output position.
                console.print_count = count
                return
            except Exception:
                pass
        console.print(line, highlight=False)

    def show_tool_result(self, result_str):
        """Complete the tool marker; verbose mode adds a dimmed result preview."""
        preview = str(result_str).replace("\n", " ")[:60]
        failed = "Error" in preview or "User denied" in preview
        variables = self._variables or {}
        verbose = bool(variables.get("verbose", False))
        if not self._pending_tools and not verbose:
            # Nothing to complete and compact mode: stay silent. Errors still
            # surface because `show_error` is a separate channel.
            return
        self._complete_tool_marker(not failed)
        if not verbose:
            return
        char_count = len(str(result_str))
        line = Text("    ↳ ", style="wash.muted")
        line.append(
            f"{preview}... ({char_count} chars)",
            style="wash.danger" if failed else "wash.muted",
        )
        self.console.print(line, highlight=False)

    def run_quiz(self, questions):
        """Launch the live quiz UI. On any failure (no TTY, prompt-toolkit
        bail, etc.) we re-raise so the caller can fall back to chat-flow
        question/answer."""
        from mu.ui.quiz_picker import run_interactive_quiz

        return run_interactive_quiz(questions)

    def ask_user_choice(
        self,
        question,
        options,
        *,
        multi_select=False,
        description="",
        allow_other=False,
    ):
        """Run the multi-choice prompt picker. On TTY failure, falls back
        to a numbered prompt on stdin/stdout so headless contexts still
        get a useful response shape."""
        try:
            from mu.ui.choice_prompt import run_interactive_choice_prompt

            return run_interactive_choice_prompt(
                question,
                list(options),
                multi_select=bool(multi_select),
                description=str(description or ""),
                allow_other=bool(allow_other),
            )
        except Exception:
            return self._fallback_choice_prompt(
                question,
                list(options),
                multi_select=bool(multi_select),
                description=str(description or ""),
                allow_other=bool(allow_other),
            )

    def _fallback_choice_prompt(
        self, question, options, *, multi_select, description, allow_other=False
    ):
        """Numbered-list prompt for when prompt-toolkit can't drive the TTY."""
        other_index = len(options) + 1 if allow_other else None
        try:
            self.console.print(f"\n[bold]{safe_markup(question)}[/bold]")
            if description:
                self.console.print(f"[wash.muted]{safe_markup(description)}[/wash.muted]")
            for i, option in enumerate(options, start=1):
                self.console.print(f"  {i}. {safe_markup(option)}")
            if other_index is not None:
                self.console.print(
                    f"  {other_index:d}. [wash.muted]Other (type your own)…[/wash.muted]"
                )
            if multi_select:
                self.console.print(
                    "[wash.muted]Enter comma-separated numbers (e.g. 1,3) or blank to cancel:[/wash.muted]"
                )
            else:
                self.console.print(
                    "[wash.muted]Enter a number, or blank to cancel:[/wash.muted]"
                )
            raw = input("> ").strip()
        except Exception:
            return {"selected": [], "other_text": "", "cancelled": True}
        if not raw:
            return {"selected": [], "other_text": "", "cancelled": True}
        picks: list[str] = []
        wants_other = False
        for token in raw.split(","):
            token = token.strip()
            if not token.isdigit():
                continue
            idx = int(token) - 1
            if other_index is not None and idx == other_index - 1:
                wants_other = True
                if not multi_select:
                    break
                continue
            if 0 <= idx < len(options):
                picks.append(options[idx])
            if not multi_select and picks:
                break
        other_text = ""
        if wants_other:
            try:
                other_text = input(f"Other ({question.rstrip('?:')}): ").strip()
            except Exception:
                other_text = ""
        return {
            "selected": picks,
            "other_text": other_text,
            "cancelled": not picks and not other_text,
        }


class _GenerationLive:
    """Context manager that owns a single Rich Live region for one agent
    generation iteration.

    Layout (top → bottom):
      * accumulating assistant text (streamed)
      * accumulating thinking text (wash.thinking) — usually empty
      * tool-call markers as they fire ("● kind  tool_name" lines)
      * status footer (spinner + the status message)

    Why one Live instead of `console.status` + bare `console.print` for
    tokens: a Rich Status is itself a Live, and `console.print(text, end="")`
    fired while a Live is active doesn't reliably keep tokens on the same
    line — each print competes with the Live's redraw and cursor tracking
    breaks (the bug the user reported: scattered tokens, status pushed
    around).  By routing every streaming source through this one Live,
    the spinner stays anchored at the bottom and text flows naturally
    above it. When the CM exits, the rendered content remains in
    terminal scrollback (transient=False).
    """

    def __init__(self, ui: "RichUI", status_message: str):
        self.ui = ui
        self._status_message = str(status_message or "")
        self._text_buf: list = []
        self._thinking_buf: list = []
        self._tool_call_log: list = []
        self._live = None
        self._lock = threading.Lock()
        self._watcher_stop = threading.Event()
        self._watcher = None
        self._spinner = Spinner("wash", style="wash.running")

    # ---------------------------------------------------------- lifecycle

    def __enter__(self):
        # Wire the UI's streaming handlers to route into us.
        self.ui._gen_live = self
        # Start of a new stream — clear the persistent flag so a previous
        # iteration's panel-suppression hint doesn't bleed forward.
        self.ui._streamed_any_text = False
        # Markers from an earlier iteration that never got a result are stale.
        self.ui._pending_tools = []
        # `transient=True` so the live streaming region clears on exit;
        # we then re-print the accumulated content as properly-rendered
        # rich Markdown (otherwise `**bold**` / headers / code fences
        # would land in scrollback as raw markdown characters).
        self._live = Live(
            get_renderable=self._render,
            console=self.ui.console,
            refresh_per_second=10,
            transient=True,
            auto_refresh=True,
        )
        self._live.start()
        # YOLO toggle watcher (Shift+Tab) still works through the same
        # update mechanism — it changes the status footer text.
        self._watcher = self.ui._start_yolo_status_watcher(
            status=self,
            base_message=self._status_message,
            stop_event=self._watcher_stop,
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        self._watcher_stop.set()
        if self._watcher is not None:
            try:
                self._watcher.join(timeout=0.2)
            except Exception:
                pass
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None
        # Live region was transient — it's gone from the terminal now.
        # Re-emit the accumulated buffers as properly-styled output so
        # final scrollback shows rendered Markdown (headers/lists/code
        # fences) instead of the raw character stream the Live was using
        # for fast token append.
        with self._lock:
            text = "".join(self._text_buf).strip()
            thinking = "".join(self._thinking_buf).strip()
            tool_calls = list(self._tool_call_log)
        try:
            if thinking:
                # Reasoning content: wash.thinking, no Markdown — partial
                # markup is common and would render erratically.
                self.ui.console.print(Text(thinking, style="wash.thinking"))
            if text:
                from .render import render_response
                render_response(text)
            # Tool markers come last so each `● kind name` line can be
            # rewritten in place as `✓ kind target 0.4s` when it completes.
            self.ui._emit_tool_markers(tool_calls)
        except Exception:
            # Never let a render bug eat the turn — fall back to raw print.
            if text:
                try:
                    self.ui.console.print(text)
                except Exception:
                    pass
        self.ui._gen_live = None
        return False

    # ----------------------------------------------- streaming sinks

    def append_text(self, text: str) -> None:
        with self._lock:
            self._text_buf.append(text)

    def append_thinking(self, text: str) -> None:
        with self._lock:
            self._thinking_buf.append(text)

    def note_tool_call(self, tool_name: str) -> None:
        with self._lock:
            self._tool_call_log.append(str(tool_name or ""))

    def update(self, new_message: str) -> None:
        """Compat with the old `console.status` API + the YOLO watcher."""
        self._status_message = str(new_message or "")

    # ------------------------------------------------- render

    # Live pulls the buffer at 10 Hz; token producers never rebuild it.
    def _render(self, *, final: bool = False):
        with self._lock:
            text = "".join(self._text_buf)
            thinking = "".join(self._thinking_buf)
            tool_calls = list(self._tool_call_log)
        parts = []
        if thinking:
            parts.append(Text(thinking, style="wash.thinking"))
        if text:
            parts.append(Text(text))
        for name in tool_calls:
            parts.append(components.tool_marker(name))
        # Bottom-anchored status footer — ONLY while the Live is active.
        # On the final render (Live exiting) we deliberately drop the
        # status: `transient=False` means the final render persists to
        # scrollback, so including the footer here leaks a duplicate
        # "Generating ... it N/1000 | ..." stub on every turn.
        if not final and self._status_message:
            self._spinner.text = Text(f" {self._status_message}", style="wash.muted")
            parts.append(self._spinner)
        return Group(*parts) if parts else Text("")
