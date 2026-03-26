# InputHandler (prompt_toolkit)
import os
import glob
import re
import json
from html import escape
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import (
    PathCompleter,
    NestedCompleter,
    Completer,
    FuzzyWordCompleter,
)
from prompt_toolkit.styles import Style
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.formatted_text import HTML

from utils.config import KNOWN_MODELS, HISTORY_DIR


MODE_PROMPT_STYLES = {
    "debug": "mode-debug",
    "feature": "mode-feature",
    "research": "mode-research",
}

MODE_CHOICES = {
    "default": None,
    "debug": None,
    "feature": None,
    "research": None,
}


def get_session_names():
    if not os.path.exists(HISTORY_DIR):
        return []
    files = glob.glob(os.path.join(HISTORY_DIR, "*.json"))
    return [os.path.basename(f).replace(".json", "") for f in files]


class DynamicSessionCompleter(Completer):
    def get_completions(self, document, complete_event):
        if not os.path.exists(HISTORY_DIR):
            return []
        files = glob.glob(os.path.join(HISTORY_DIR, "*.json"))
        sessions = [os.path.basename(f).replace(".json", "") for f in files]

        completer = FuzzyWordCompleter(sessions)
        yield from completer.get_completions(document, complete_event)


class DynamicVariableCompleter(Completer):
    def __init__(self, input_handler):
        self.input_handler = input_handler

    def get_completions(self, document, complete_event):
        if self.input_handler.variables_dict is None:
            return
        # We always get the latest keys from the dictionary reference
        completer = FuzzyWordCompleter(list(self.input_handler.variables_dict.keys()))
        yield from completer.get_completions(document, complete_event)


class DynamicFeatureIdCompleter(Completer):
    def get_completions(self, document, complete_event):
        feature_ids = set()

        # Workspace feature directories: documentation/feature_req_<feature_id>
        for path in glob.glob(os.path.join("documentation", "feature_req_*")):
            if not os.path.isdir(path):
                continue
            name = os.path.basename(path)
            if name.startswith("feature_req_"):
                feature_ids.add(name.replace("feature_req_", "", 1))

        # Session-managed feature metadata records.
        for path in glob.glob(
            os.path.join(HISTORY_DIR, "sessions", "*", "features", "*.json")
        ):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                feature_id = str(payload.get("feature_id", "")).strip()
                if feature_id:
                    feature_ids.add(feature_id)
            except (OSError, json.JSONDecodeError, AttributeError):
                continue

        if not feature_ids:
            return

        completer = FuzzyWordCompleter(sorted(feature_ids))
        yield from completer.get_completions(document, complete_event)


class MergedCompleter(Completer):
    """Custom class to merge multiple completers to avoid import errors across versions."""

    def __init__(self, completers):
        self.completers = completers

    def get_completions(self, document, complete_event):
        for completer in self.completers:
            yield from completer.get_completions(document, complete_event)


class InputHandler:
    def __init__(self):
        self.variables_dict = None  # Will be set via set_variables
        path_completer = PathCompleter(expanduser=True)
        directory_completer = PathCompleter(expanduser=True, only_directories=True)
        session_completer = DynamicSessionCompleter()
        variable_completer = DynamicVariableCompleter(self)
        feature_id_completer = DynamicFeatureIdCompleter()

        model_dict = {m: None for m in KNOWN_MODELS}

        provider_completer = NestedCompleter.from_nested_dict(
            {"gemini": None, "ollama": None, "openai": None}
        )

        tool_completer = NestedCompleter.from_nested_dict(
            {
                "enable": None,
                "disable": None,
                "list": None,
            }
        )

        feature_completer = NestedCompleter.from_nested_dict(
            {
                "list": None,
                "new": None,
                "load": feature_id_completer,
                "delete": feature_id_completer,
                "status": feature_id_completer,
                "phases": feature_id_completer,
            }
        )

        memory_completer = NestedCompleter.from_nested_dict(
            {
                "status": None,
                "list": {"task": None, "scratchpad": None, "all": None},
                "ls": {"task": None, "scratchpad": None, "all": None},
                "clear": {"task": None, "scratchpad": None, "all": None},
            }
        )

        mode_completer = NestedCompleter.from_nested_dict(MODE_CHOICES)
        folder_completer = MergedCompleter(
            [
                NestedCompleter.from_nested_dict({"remove": directory_completer}),
                directory_completer,
            ]
        )

        self.command_completions = {
            "/help": None,
            "/h": None,
            "/clear": None,
            "/c": None,
            "/clearfiles": None,
            "/cf": None,
            "/view": None,
            "/v": None,
            "/quit": None,
            "/exit": None,
            "/q": None,
            "/file": path_completer,
            "/f": path_completer,
            "/add": path_completer,
            "/folder": folder_completer,
            "/dir": folder_completer,
            "/model": model_dict,
            "/provider": provider_completer,
            "/agentic": None,
            "/mode": mode_completer,
            "/feature": feature_completer,
            "/memory": memory_completer,
            "/tool": tool_completer,
            "/tools": tool_completer,
            "/system": None,
            "/sys": None,
            "/thinking": None,
            "/list": None,
            "/ls": None,
            "/load": session_completer,
            "/open": session_completer,
            "/new": None,
            "/delete": session_completer,
            "/rm": session_completer,
            "/stats": None,
            "/splash": None,
            "/set": variable_completer,
            "/get": variable_completer,
            "/unset": variable_completer,
            "/variables": None,
            "/flush": None,
            "/yolo": None,
        }

        self.completer = NestedCompleter.from_nested_dict(self.command_completions)

        self.style = Style.from_dict(
            {
                "prompt": "ansiblue bold",
                "rprompt": "bg:ansiblue ansiwhite",
                "files": "ansiyellow",
                "yolo-indicator": "ansiyellow bold blink",
                "mode-debug": "ansiyellow bold",
                "mode-feature": "ansiblue bold",
                "mode-research": "ansimagenta bold",
            }
        )

        history_file = os.path.expanduser("~/.mucli_history")

        self.kb = KeyBindings()

        @self.kb.add("enter")
        def _(event):
            buff = event.current_buffer
            text = buff.text.strip()
            if text.startswith("/"):
                buff.validate_and_handle()
            else:
                buff.insert_text("\n")

        @self.kb.add("escape", "enter")
        def _(event):
            event.current_buffer.validate_and_handle()

        @self.kb.add("s-tab")
        def _(event):
            self.toggle_yolo_mode()
            if event.app:
                event.app.invalidate()

        self.session = PromptSession(
            history=FileHistory(history_file),
            auto_suggest=AutoSuggestFromHistory(),
            completer=self.completer,
            style=self.style,
            key_bindings=self.kb,
            multiline=True,
        )

    def set_variables(self, variables_dict):
        """Update the reference to the variables dictionary for completion."""
        self.variables_dict = variables_dict

    def is_yolo_enabled(self):
        if self.variables_dict is None:
            return False
        return bool(self.variables_dict.get("yolo", False))

    def toggle_yolo_mode(self):
        if self.variables_dict is None:
            return False
        enabled = not bool(self.variables_dict.get("yolo", False))
        self.variables_dict["yolo"] = enabled
        return enabled

    @staticmethod
    def _progress_bar(done, total, width=8):
        total = max(1, int(total or 1))
        done = max(0, min(int(done or 0), total))
        ratio = done / total
        filled = min(width, int(round(width * ratio)))
        percent = int(round(ratio * 100))
        return f"{'█' * filled}{'░' * (width - filled)} {percent:>3}%"

    def build_prompt_markup(
        self,
        session_name,
        staged_files,
        agent_mode="default",
        current_task=None,
        feature_context=None,
    ):
        files_text = ""
        if staged_files:
            # Note the updated accessor here for our new FileReference schema
            f_names = ", ".join([f["file_ref"]["display_name"] for f in staged_files])
            files_text = f" [Files: {f_names}]"

        mode_name = str(agent_mode or "default").lower()
        mode_text = ""
        if mode_name != "default":
            mode_style = MODE_PROMPT_STYLES.get(mode_name, "prompt")
            mode_text = f" <{mode_style}>{mode_name}</{mode_style}>"

        yolo_text = ""
        if self.is_yolo_enabled():
            yolo_text = " <yolo-indicator>✦</yolo-indicator>"

        task_text = ""
        if current_task:
            task = str(current_task).strip()
            if len(task) > 48:
                task = f"{task[:45]}…"
            task_text = f" <files>[Task: {escape(task)}]</files>"

        feature_text = ""
        if isinstance(feature_context, dict):
            phase_bar = self._progress_bar(
                feature_context.get("phase_done", 0),
                feature_context.get("phase_total", 1),
            )
            overall_bar = self._progress_bar(
                feature_context.get("overall_done", 0),
                feature_context.get("overall_total", 1),
            )
            feature_text = (
                f" <files>[P {phase_bar} | O {overall_bar}]</files>"
            )

        return (
            f"<prompt>[{session_name}]</prompt>"
            f"{mode_text}"
            f"{yolo_text}"
            f"{task_text}"
            f"{feature_text}"
            f"<files>{files_text}</files>\n"
            f"<prompt>>></prompt> "
        )

    def build_input_toolbar_text(self):
        yolo_status = "ON" if self.is_yolo_enabled() else "OFF"
        return (
            "[Meta+Enter] or [Esc] [Enter] to submit | "
            f"[Shift+Tab] toggles YOLO ({yolo_status}) | "
            "/help for commands"
        )

    def build_choice_toolbar_text(self):
        yolo_status = "ON" if self.is_yolo_enabled() else "OFF"
        return f"[Shift+Tab] toggles YOLO ({yolo_status})"

    def get_input(
        self,
        session_name,
        staged_files,
        agent_mode="default",
        current_task=None,
        feature_context=None,
    ):
        message = HTML(
            self.build_prompt_markup(
                session_name,
                staged_files,
                agent_mode=agent_mode,
                current_task=current_task,
                feature_context=feature_context,
            )
        )

        def bottom_toolbar():
            return self.build_input_toolbar_text()

        try:
            return self.session.prompt(
                message,
                bottom_toolbar=bottom_toolbar,
                prompt_continuation=self._prompt_continuation,
            ).strip()
        except KeyboardInterrupt:
            return ""
        except EOFError:
            raise EOFError

    def prompt_choice(self, prompt_text, *, choices, default=None):
        plain_prompt = re.sub(r"\[[^\]]+\]", "", str(prompt_text)).strip()
        choices_str = "/".join(str(choice) for choice in choices)
        default_suffix = f" default={default}" if default else ""
        message = HTML(
            f"<prompt>{plain_prompt} [{choices_str}]{default_suffix}</prompt> "
        )

        def bottom_toolbar():
            return self.build_choice_toolbar_text()

        while True:
            value = self.session.prompt(
                message,
                bottom_toolbar=bottom_toolbar,
                multiline=False,
                default=default or "",
            ).strip()
            if value in choices:
                return value

    def _prompt_continuation(self, width, line_number, is_soft_wrap):
        return HTML("<prompt>    </prompt>")
