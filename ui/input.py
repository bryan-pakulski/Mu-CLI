# InputHandler (prompt_toolkit)
import os
import glob
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
    "git": "mode-git",
}

MODE_CHOICES = {
    "default": None,
    "debug": None,
    "feature": None,
    "research": None,
    "git": None,
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
                "mode-debug": "ansiyellow bold",
                "mode-feature": "ansiblue bold",
                "mode-research": "ansimagenta bold",
                "mode-git": "ansigreen bold",
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

    def build_prompt_markup(self, session_name, staged_files, agent_mode="default"):
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

        return (
            f"<prompt>[{session_name}]</prompt>"
            f"{mode_text}"
            f"<files>{files_text}</files> "
            f"<prompt>>>></prompt> "
        )

    def get_input(self, session_name, staged_files, agent_mode="default"):
        message = HTML(
            self.build_prompt_markup(
                session_name, staged_files, agent_mode=agent_mode
            )
        )

        def bottom_toolbar():
            return HTML(
                " <b>[Meta+Enter]</b> or <b>[Esc] [Enter]</b> to submit | <b>/help</b> for commands"
            )

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

    def _prompt_continuation(self, width, line_number, is_soft_wrap):
        return HTML(f'<prompt>{("." * (width - 1)) + " "}</prompt>')
