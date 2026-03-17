# Session and SessionManager (history state)
import os
import json
import time
import glob
from datetime import datetime

from core.workspace import FolderContext
from providers.base import LLMProvider, Message, MessagePart, FileReference
from core.tools import TOOLS, execute_tool
from utils.helpers import get_safe_mime_type, display_image_in_terminal
from utils.config import (
    HISTORY_DIR,
    DEFAULT_SESSION_NAME,
    calculate_cost,
    AGENTIC_SYSTEM_BASE,
    AGENTIC_MODES,
    DEFAULT_VARIABLES,
    validate_and_cast,
)

class SessionManager:
    def __init__(self, ui=None):
        self.ui = ui
        self.current_session_name = DEFAULT_SESSION_NAME
        self.history = []  # Stores standardized list of dicts representing messages
        self.summary_anchor = 0
        self.folder_context_data = {}
        self.variables = DEFAULT_VARIABLES.copy()
        self._load_session(DEFAULT_SESSION_NAME)

    def _get_filepath(self, name):
        return os.path.join(HISTORY_DIR, f"{name}.json")

    def _load_session(self, name):
        filepath = self._get_filepath(name)
        self.current_session_name = name
        self.history = []
        self.summary_anchor = 0
        self.folder_context_data = {}
        self.variables.clear()
        self.variables.update(DEFAULT_VARIABLES)

        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.history = data
                elif isinstance(data, dict):
                    self.history = data.get("history", [])
                    self.summary_anchor = data.get("summary_anchor", 0)
                    self.folder_context_data = data.get("folder_context", {})
                    
                    saved_vars = data.get("variables", {})
                    for k, v in saved_vars.items():
                        try:
                            self.variables[k] = validate_and_cast(k, v)
                        except ValueError:
                            # If saved data is corrupt or schema changed, keep default
                            pass
            except (json.JSONDecodeError, IOError):
                self.history = []

    def save_history(self, folder_context_obj=None):
        filepath = self._get_filepath(self.current_session_name)
        try:
            data = {
                "history": self.history,
                "summary_anchor": self.summary_anchor,
                "folder_context": (
                    folder_context_obj.to_dict() if folder_context_obj else {}
                ),
                "variables": self.variables,
            }
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            if self.ui: self.ui.show_error(f"Warning: Could not save chat history: {e}")

    def switch_session(self, name):
        self.save_history()
        self._load_session(name)
        if self.ui: self.ui.show_info(f"Switched to session: '{name}'")
        self.view_history()

    def new_session(self, name=None):
        self.save_history()
        if not name:
            name = f"chat_{int(time.time())}"
        self.current_session_name = name
        self.history = []
        self.variables.clear()
        self.variables.update(DEFAULT_VARIABLES)
        self.save_history()
        if self.ui: self.ui.show_info(f"Started new session: '{name}'")

    def list_sessions(self):
        if not os.path.exists(self._get_filepath(self.current_session_name)):
            self.save_history()

        files = glob.glob(os.path.join(HISTORY_DIR, "*.json"))
        if self.ui:
            # We might want a specific UI method for listing sessions
            self.ui.show_info("\n=== Available Conversations ===")
            for f in files:
                name = os.path.basename(f).replace(".json", "")
                indicator = "*" if name == self.current_session_name else " "
                mod_time = datetime.fromtimestamp(os.path.getmtime(f)).strftime(
                    "%Y-%m-%d %H:%M"
                )
                self.ui.show_info(f" {indicator} {name:<20} ({mod_time})")

    def delete_session(self, name):
        if name == self.current_session_name:
            if self.ui: self.ui.show_error("Cannot delete active session.")
            return

        filepath = self._get_filepath(name)
        if os.path.exists(filepath):
            os.remove(filepath)
            if self.ui: self.ui.show_info(f"Deleted session: '{name}'")
        else:
            if self.ui: self.ui.show_error(f"Session '{name}' not found.")

    def clear_current_history(self):
        self.history = []
        self.save_history()
        if self.ui: self.ui.show_info("Current chat history cleared.")

    def view_history(self):
        if not self.history:
            if self.ui: self.ui.show_info("No history in this session.")
            return

        if self.ui:
            self.ui.show_info(f"\nConversation History ({self.current_session_name})\n")

            for turn in self.history:
                role = turn["role"]
                for part in turn.get("parts", []):
                    p_type = part.get("type")
                    if p_type == "text":
                        self.ui.render_message(role, part["text"])
                    elif p_type == "file":
                        mime = part.get("file_ref", {}).get("mime_type", "file")
                        self.ui.show_info(f"[Attached File: {mime}]")
                    elif p_type == "tool_call":
                        self.ui.show_info(f"  [Tool Call: {part.get('tool_name')}]")
                    elif p_type == "tool_result":
                        res_preview = str(part.get("tool_result", ""))[:50].replace(
                            "\n", ""
                        )
                        self.ui.show_info(f"  [Tool Result: {res_preview}...]")


class Session:
    def __init__(
        self,
        provider: LLMProvider,
        thinking: bool,
        system_instruction: str,
        session_manager: SessionManager,
        ui=None,
        debug: bool = False,
    ):
        self.provider = provider
        self.thinking = thinking
        self.system_instruction = system_instruction
        self.session_manager = session_manager
        self.ui = ui
        self.debug = debug
        self.variables = session_manager.variables
        self.agentic = True
        self.active_context_window = 150
        self.staged_files = []  # list of dicts
        self.disabled_tools = []  # list of tool names strings

        self.folder_context = FolderContext()
        if session_manager.folder_context_data:
            self.folder_context.from_dict(session_manager.folder_context_data)
            if self.folder_context.folders:
                if self.ui:
                    self.ui.show_info(
                        f"Restored folder context: {', '.join(self.folder_context.folders)}"
                    )

    def add_file(self, file_path):
        file_path = file_path.strip("'\"")
        file_path = os.path.expanduser(file_path)

        if not os.path.exists(file_path):
            if self.ui: self.ui.show_error(f"Error: File '{file_path}' not found.")
            return

        safe_mime = get_safe_mime_type(file_path)
        if self.ui: self.ui.show_info(f"Uploading {file_path} as {safe_mime}...")

        try:
            file_ref = self.provider.upload_file(file_path, safe_mime)
            if file_ref:
                self.staged_files.append(
                    {
                        "type": "file",
                        "file_ref": {
                            "uri": file_ref.uri,
                            "mime_type": file_ref.mime_type,
                            "display_name": file_ref.display_name,
                        },
                    }
                )
                if self.ui: self.ui.show_info("Upload complete.")
        except Exception as e:
            if self.ui: self.ui.show_error(f"Upload failed: {e}")

    def clear_files(self):
        self.staged_files = []
        if self.ui: self.ui.show_info("Staged files cleared.")

    def _build_messages_from_history(
        self, recent_history_dicts, new_user_message_dict
    ) -> list[Message]:
        messages = []
        for msg_dict in recent_history_dicts + [new_user_message_dict]:
            parts = []
            for p in msg_dict.get("parts", []):
                if p.get("type") == "text":
                    parts.append(MessagePart(type="text", text=p["text"]))
                elif p.get("type") == "file":
                    fr_data = p.get("file_ref", {})
                    fr = FileReference(
                        uri=fr_data.get("uri"),
                        mime_type=fr_data.get("mime_type"),
                        display_name=fr_data.get("display_name"),
                    )
                    parts.append(MessagePart(type="file", file_ref=fr))
                elif p.get("type") == "tool_call":
                    parts.append(
                        MessagePart(
                            type="tool_call",
                            tool_name=p["tool_name"],
                            tool_args=p.get("tool_args", {}),
                            thought_signature=p.get("thought_signature"),
                        )
                    )
                elif p.get("type") == "tool_result":
                    parts.append(
                        MessagePart(
                            type="tool_result",
                            tool_name=p.get("tool_name", "tool"),
                            tool_result=p.get("tool_result", ""),
                            thought_signature=p.get("thought_signature"),
                        )
                    )
            messages.append(Message(role=msg_dict["role"], parts=parts))
        return messages

    def send_message(self, text):
        parts = list(self.staged_files)
        if text:
            parts.append({"type": "text", "text": text})

        new_user_message = {"role": "user", "parts": parts}

        if text and self.ui:
            self.ui.render_message("user", text)

        workspace_context = ""
        if self.folder_context.folders:
            if self.agentic:
                active_tools = [t for t in TOOLS if t.name not in self.disabled_tools]
                tool_desc_str = "\n".join(
                    [f"{t.name} - {t.description}" for t in active_tools]
                )

                agent_mode = str(self.variables.get("agent_mode", "default")).lower()
                mode_instruction = AGENTIC_MODES.get(
                    agent_mode, AGENTIC_MODES["default"]
                )

                map_str = self.folder_context.get_tree_map()
                workspace_context = f"<workspace_map>\n{map_str}\n</workspace_map>\n\n{AGENTIC_SYSTEM_BASE.format(tool_descriptions=tool_desc_str)}\n\n### CURRENT STRATEGY MODE: {agent_mode.upper()}\n{mode_instruction}"
            else:
                if self.ui:
                    with self.ui.show_status("Scanning monitored folders for changes..."):
                        folder_initial_xml = self.folder_context.get_initial_context_xml()
                        folder_diff_xml = self.folder_context.get_context_diff_xml()
                        workspace_context = f"{folder_initial_xml}\n\n{folder_diff_xml}"

        dynamic_system_prompt = self.system_instruction
        if workspace_context:
            dynamic_system_prompt += f"\n\n{workspace_context}"

        recent_history = self.session_manager.history[-self.active_context_window :]
        messages = self._build_messages_from_history(recent_history, new_user_message)

        self.session_manager.history.append(new_user_message)
        self.session_manager.save_history()
        self.staged_files = []

        max_iterations = self.variables.get("max_iterations", 50)
        iteration = 0
        active_tools = [t for t in TOOLS if t.name not in self.disabled_tools]

        while iteration < max_iterations:
            iteration += 1

            try:
                status_msg = f"Generating ({self.provider.model_name}) it {iteration}/{max_iterations}..."
                if self.ui:
                    with self.ui.show_status(status_msg):
                        response = self.provider.generate(
                            messages=messages,
                            system_prompt=dynamic_system_prompt,
                            thinking=self.thinking,
                            tools=(
                                active_tools
                                if (self.folder_context.folders and self.agentic)
                                else None
                            ),
                        )
                else:
                    response = self.provider.generate(
                        messages=messages,
                        system_prompt=dynamic_system_prompt,
                        thinking=self.thinking,
                        tools=(
                            active_tools
                            if (self.folder_context.folders and self.agentic)
                            else None
                        ),
                    )

                ai_parts_archive = []
                has_tool_call = False
                has_text = False

                for part in response.parts:
                    if part.type == "text" and part.text:
                        has_text = True
                        if self.ui:
                            self.ui.render_message("assistant", part.text, self.provider.model_name)
                        ai_parts_archive.append({"type": "text", "text": part.text})

                    elif part.type == "image_inline" and part.inline_data:
                        display_image_in_terminal(part.inline_data)
                        ai_parts_archive.append(
                            {
                                "type": "text",
                                "text": "[Image Generated and Saved locally]",
                            }
                        )

                    elif part.type == "tool_call":
                        has_tool_call = True
                        ai_parts_archive.append(
                            {
                                "type": "tool_call",
                                "tool_name": part.tool_name,
                                "tool_args": part.tool_args,
                                "thought_signature": part.thought_signature,
                            }
                        )
                        if self.ui: self.ui.show_info(f"🔨 Running tool: {part.tool_name}({part.tool_args})")

                if ai_parts_archive:
                    self.session_manager.history.append(
                        {"role": "assistant", "parts": ai_parts_archive}
                    )

                cost_str = ""
                est_cost = calculate_cost(
                    self.provider.model_name,
                    response.input_tokens,
                    response.output_tokens,
                )
                if est_cost is not None:
                    cost_str = f"| Est. Cost: ${est_cost:.5f}"
                
                if self.ui:
                    self.ui.show_info(
                        f"Tokens: In {response.input_tokens} | Out {response.output_tokens} | Total {response.total_tokens} {cost_str}"
                    )

                if not has_tool_call:
                    if not has_text:
                        nudge_msg = {
                            "role": "user",
                            "parts": [
                                {
                                    "type": "text",
                                    "text": "You have completed your tool executions but provided no textual response. Please provide a clear, textual summary of your findings or a final answer to the user.",
                                }
                            ],
                        }
                        self.session_manager.history.append(nudge_msg)
                        messages = self._build_messages_from_history(
                            self.session_manager.history[-self.active_context_window :],
                            {"role": "system", "parts": []},
                        )[:-1]
                        continue
                    self.session_manager.save_history(self.folder_context)
                    break

                auto_approve = self.variables.get("auto_approve", True)
                tool_result_parts = []

                for part in response.parts:
                    if part.type == "tool_call":
                        tool_def = next(
                            (t for t in TOOLS if t.name == part.tool_name), None
                        )
                        needs_approval = not auto_approve or (
                            tool_def and tool_def.requires_approval
                        )
                        if needs_approval:
                            # This part is tricky as it needs user interaction.
                            # We might need a self.ui.ask_approval() method
                            from rich.prompt import Prompt
                            choice = Prompt.ask(
                                f"\n[bold yellow]Permission Required[/bold yellow] for tool: [cyan]{part.tool_name}[/cyan]\nArgs: {part.tool_args}\nAllow?",
                                choices=["y", "n", "e"],
                                default="y",
                            )
                            if choice == "n":
                                result = "User denied this tool call."
                            elif choice == "e":
                                reason = Prompt.ask(
                                    "Provide an explanation to the model"
                                )
                                result = f"User denied this tool call. Reason: {reason}"
                            else:
                                result = execute_tool(
                                    part.tool_name, part.tool_args, self.folder_context
                                )
                        else:
                            result = execute_tool(
                                part.tool_name, part.tool_args, self.folder_context
                            )

                        res_preview = str(result).replace("\n", " ")[:60]
                        if self.ui: self.ui.show_info(f"  ↳ Result: {res_preview}... ({len(str(result))} chars)")

                        tool_result_parts.append(
                            {
                                "type": "tool_result",
                                "tool_name": part.tool_name,
                                "tool_result": result,
                                "thought_signature": part.thought_signature,
                            }
                        )

                tool_result_msg = {"role": "tool", "parts": tool_result_parts}
                self.session_manager.history.append(tool_result_msg)
                self.session_manager.save_history(self.folder_context)

                messages = self._build_messages_from_history(
                    self.session_manager.history[-self.active_context_window :],
                    {"role": "system", "parts": []},
                )[:-1]

            except KeyboardInterrupt:
                if self.ui: self.ui.show_info("\nAgentic loop interrupted by user.")
                self.session_manager.history.append(
                    {
                        "role": "tool",
                        "parts": [
                            {
                                "type": "tool_result",
                                "tool_name": "system",
                                "tool_result": "User interrupted execution.",
                            }
                        ],
                    }
                )
                self.session_manager.save_history(self.folder_context)
                break
            except Exception as e:
                if self.ui: self.ui.show_error(f"API Error during agentic loop: {e}")
                self.session_manager.save_history(self.folder_context)
                break
