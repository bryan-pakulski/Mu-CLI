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


class InputHandler:
    def __init__(self):
        path_completer = PathCompleter(expanduser=True)
        session_completer = DynamicSessionCompleter()

        model_dict = {m: None for m in KNOWN_MODELS}

        folder_completer = NestedCompleter.from_nested_dict(
            {
                "remove": path_completer,
            }
        )

        provider_completer = NestedCompleter.from_nested_dict(
            {"gemini": None, "ollama": None}
        )

        tool_completer = NestedCompleter.from_nested_dict(
            {
                "enable": None,
                "disable": None,
                "list": None,
            }
        )

        mode_completer = NestedCompleter.from_nested_dict(
            {"default": None, "debug": None, "feature": None, "research": None}
        )

        self.completer = NestedCompleter.from_nested_dict(
            {
                "/file": path_completer,
                "/f": path_completer,
                "/clear": None,
                "/clearfiles": None,
                "/view": None,
                "/quit": None,
                "/exit": None,
                "/help": None,
                "/model": model_dict,
                "/agentic": None,
                "/folder": {
                    "remove": folder_completer,
                    **{".": path_completer},
                },
                "/provider": provider_completer,
                "/tool": tool_completer,
                "/mode": mode_completer,
                "/system": None,
                "/thinking": None,
                "/list": None,
                "/load": session_completer,
                "/new": None,
                "/delete": session_completer,
                "/tokens": None,
            }
        )

        self.style = Style.from_dict(
            {
                "prompt": "ansicyan bold",
                "rprompt": "bg:ansiblue ansiwhite",
                "files": "ansiyellow",
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

    def get_input(self, session_name, staged_files):
        files_text = ""
        if staged_files:
            # Note the updated accessor here for our new FileReference schema
            f_names = ", ".join([f["file_ref"]["display_name"] for f in staged_files])
            files_text = f" [Files: {f_names}]"

        message = HTML(
            f"<prompt>[{session_name}]</prompt>"
            f"<files>{files_text}</files> "
            f"<prompt>>>></prompt> "
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
