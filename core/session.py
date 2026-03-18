# Session and SessionManager (history state)
import os
import json
import time
import glob
from datetime import datetime

from core.workspace import FolderContext
from providers.base import LLMProvider, Message, MessagePart, FileReference
from core.tools import TOOLS, execute_tool, get_modifications
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


def _shorten_tool_args(args: dict) -> dict:
    """Shortens long string arguments (like 'content' or 'diff') for display."""
    if not args:
        return {}
    shortened = args.copy()
    for key in ["content", "diff"]:
        if (
            key in shortened
            and isinstance(shortened[key], str)
            and len(shortened[key]) > 100
        ):
            shortened[key] = f"({len(shortened[key])} chars)"
    return shortened


class SessionManager:
    def __init__(self, ui=None, session_name=None):
        self.ui = ui
        self.current_session_name = DEFAULT_SESSION_NAME
        self.history = []  # Stores standardized list of dicts representing messages
        self.provider_config = {}  # Stores { "provider": "...", "model": "..." }
        self.summary_anchor = 0
        self.folder_context = FolderContext()
        self.token_counts = {"input": 0, "output": 0, "total": 0, "total_cost": 0.0}
        self.variables = DEFAULT_VARIABLES.copy()

        if session_name:
            self._load_session(session_name)
        else:
            self._load_session(DEFAULT_SESSION_NAME)

    def _get_filepath(self, name):
        return os.path.join(HISTORY_DIR, f"{name}.json")

    def _load_session(self, name):
        filepath = self._get_filepath(name)
        self.current_session_name = name
        self.history = []
        self.summary_anchor = 0
        self.provider_config = {}
        self.folder_context = FolderContext()
        self.variables.clear()
        self.token_counts = {"input": 0, "output": 0, "total": 0, "total_cost": 0.0}
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
                    self.provider_config = data.get("provider_config", {})
                    self.folder_context.from_dict(data.get("folder_context", {}))
                    self.token_counts = data.get(
                        "token_counts",
                        {"input": 0, "output": 0, "total": 0, "total_cost": 0.0},
                    )

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
        if folder_context_obj:
            self.folder_context = folder_context_obj

        try:
            data = {
                "history": self.history,
                "summary_anchor": self.summary_anchor,
                "provider_config": self.provider_config,
                "folder_context": self.folder_context.to_dict(),
                "variables": self.variables,
                "token_counts": self.token_counts,
            }
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            if self.ui:
                self.ui.show_error(f"Warning: Could not save chat history: {e}")

    def switch_session(self, name):
        self.save_history()
        self._load_session(name)
        if self.ui:
            self.ui.show_info(f"Switched to session: '{name}'")
        self.view_history()

    def new_session(self, name=None, provider_name=None, model_name=None):
        self.save_history()
        if not name:
            name = f"chat_{int(time.time())}"
        self.folder_context = FolderContext()
        self.current_session_name = name
        self.history = []
        self.provider_config = {"provider": provider_name, "model": model_name}
        self.token_counts = {"input": 0, "output": 0, "total": 0, "total_cost": 0.0}
        self.variables.clear()
        self.variables.update(DEFAULT_VARIABLES)
        self.save_history()
        if self.ui:
            self.ui.show_info(f"Started new session: '{name}'")

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

    def get_session_list(self):
        files = glob.glob(os.path.join(HISTORY_DIR, "*.json"))
        sessions = []
        for f in files:
            sessions.append(os.path.basename(f).replace(".json", ""))
        return sorted(sessions)

    def delete_session(self, name):
        if name == self.current_session_name:
            if self.ui:
                self.ui.show_error("Cannot delete active session.")
            return

        filepath = self._get_filepath(name)
        if os.path.exists(filepath):
            os.remove(filepath)
            if self.ui:
                self.ui.show_info(f"Deleted session: '{name}'")
        else:
            if self.ui:
                self.ui.show_error(f"Session '{name}' not found.")

    def clear_current_history(self):
        self.history = []
        self.save_history()
        if self.ui:
            self.ui.show_info("Current chat history cleared.")

    def view_history(self):
        if not self.history:
            if self.ui:
                self.ui.show_info("No history in this session.")
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

    def compact_completed_turn(self):
        """
        Collapses the most recent agentic turn.
        Identifies the last 'user' prompt and the last 'assistant' text response,
        then removes all intermediate tool calls and results between them.
        """
        if len(self.history) < 2:
            return

        # 1. Find the index of the last 'user' message that started this turn
        last_user_idx = -1
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i]["role"] == "user":
                last_user_idx = i
                break

        if last_user_idx == -1:
            return

        # 2. Extract the final assistant text parts from the end of history
        final_assistant_parts = []
        for i in range(len(self.history) - 1, last_user_idx, -1):
            if self.history[i]["role"] == "assistant":
                # Collect text parts only
                text_parts = [
                    p for p in self.history[i]["parts"] if p["type"] == "text"
                ]
                if text_parts:
                    # We reverse them back because we are iterating backwards
                    final_assistant_parts = text_parts + final_assistant_parts
                    # If we found the "final" response message, we stop looking for more text
                    break

        # 3. Reconstruct history
        # Keep everything before the current turn
        new_history = self.history[: last_user_idx + 1]

        # Append the collapsed assistant response if we found text
        if final_assistant_parts:
            new_history.append({"role": "assistant", "parts": final_assistant_parts})

        self.history = new_history
        self.summary_anchor = 0


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

        self.folder_context = session_manager.folder_context
        if self.folder_context.folders:
            if self.ui:
                self.ui.show_info(
                    f"Restored folder context: {', '.join(self.folder_context.folders)}"
                )
            try:
                os.chdir(self.folder_context.folders[0])
                if self.ui:
                    self.ui.show_info(f"Working directory set to: {os.getcwd()}")
            except Exception:
                pass

    def add_file(self, file_path):
        file_path = file_path.strip("'\"")
        file_path = os.path.expanduser(file_path)

        if not os.path.exists(file_path):
            if self.ui:
                self.ui.show_error(f"Error: File '{file_path}' not found.")
            return

        safe_mime = get_safe_mime_type(file_path)
        if self.ui:
            self.ui.show_info(f"Uploading {file_path} as {safe_mime}...")

        try:
            file_ref = self.provider.upload_file(file_path, safe_mime)
            if file_ref:
                self.staged_files.append({
                    "type": "file",
                    "file_ref": {
                        "uri": file_ref.uri,
                        "mime_type": file_ref.mime_type,
                        "display_name": file_ref.display_name,
                    },
                })
                if self.ui:
                    self.ui.show_info("Upload complete.")
        except Exception as e:
            if self.ui:
                self.ui.show_error(f"Upload failed: {e}")

    def clear_files(self):
        self.staged_files = []
        if self.ui:
            self.ui.show_info("Staged files cleared.")

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
                tool_desc_str = "\n".join([
                    f"{t.name} - {t.description}" for t in active_tools
                ])

                agent_mode = str(self.variables.get("agent_mode", "default")).lower()
                mode_instruction = AGENTIC_MODES.get(
                    agent_mode, AGENTIC_MODES["default"]
                )

                map_str = self.folder_context.get_tree_map()
                workspace_context = f"<workspace_map>\n{map_str}\n</workspace_map>\n\n{AGENTIC_SYSTEM_BASE.format(tool_descriptions=tool_desc_str)}\n\n### CURRENT STRATEGY MODE: {agent_mode.upper()}\n{mode_instruction}"
            else:
                if self.ui:
                    with self.ui.show_status(
                        "Scanning monitored folders for changes..."
                    ):
                        folder_initial_xml = (
                            self.folder_context.get_initial_context_xml()
                        )
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

        total_in = 0
        total_out = 0
        total_cost = 0.0

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
                            self.ui.render_message(
                                "assistant", part.text, self.provider.model_name
                            )
                        ai_parts_archive.append({"type": "text", "text": part.text})

                    elif part.type == "image_inline" and part.inline_data:
                        display_image_in_terminal(part.inline_data)
                        ai_parts_archive.append({
                            "type": "text",
                            "text": "[Image Generated and Saved locally]",
                        })

                    elif part.type == "tool_call":
                        has_tool_call = True
                        ai_parts_archive.append({
                            "type": "tool_call",
                            "tool_name": part.tool_name,
                            "tool_args": part.tool_args,
                            "thought_signature": part.thought_signature,
                        })
                        if self.ui:
                            self.ui.show_info(
                                f"🔨 Running tool: {part.tool_name}({_shorten_tool_args(part.tool_args)})"
                            )

                if ai_parts_archive:
                    self.session_manager.history.append({
                        "role": "assistant",
                        "parts": ai_parts_archive,
                    })

                self.session_manager.token_counts["input"] += response.input_tokens
                self.session_manager.token_counts["output"] += response.output_tokens
                self.session_manager.token_counts["total"] += response.total_tokens

                total_in += response.input_tokens
                total_out += response.output_tokens

                est_cost = calculate_cost(
                    self.provider.model_name,
                    response.input_tokens,
                    response.output_tokens,
                )
                cost_str = ""
                if est_cost is not None:
                    total_cost += est_cost
                    self.session_manager.token_counts["total_cost"] += est_cost
                    cost_str = (
                        f"| Est. Cost: ${est_cost:.5f} (Total: ${total_cost:.5f})"
                    )

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

                    if self.ui:
                        self.ui.show_info(
                            f"Final session tokens: In {total_in} | Out {total_out} | Total {total_in + total_out} | Total Est. Cost: ${total_cost:.5f}"
                        )

                    if self.variables.get("compact_history", False):
                        if self.ui:
                            self.ui.show_info(
                                "[dim]Compacting turn history (removing tool metadata)...[/dim]"
                            )
                            self.session_manager.compact_completed_turn()

                    self.session_manager.save_history(self.folder_context)
                    break

                strict_mode = self.variables.get("strict_mode", False)
                tool_result_parts = []
                tool_calls = [p for p in response.parts if p.type == "tool_call"]

                # Pre-calculate modifications for tools needing approval
                to_approve_data = {}
                for idx, part in enumerate(tool_calls):
                    tool_def = next(
                        (t for t in TOOLS if t.name == part.tool_name), None
                    )

                    if self.variables.get("yolo", False):
                        continue

                    if strict_mode or (tool_def and tool_def.requires_approval):
                        mods = get_modifications(
                            part.tool_name, part.tool_args, self.folder_context
                        )
                        to_approve_data[idx] = mods

                # Show bulk diffs if multiple
                if len(to_approve_data) > 1:
                    if self.ui:
                        self.ui.show_info(
                            f"\n[bold yellow]Turn contains {len(to_approve_data)} modifications requiring approval.[/bold yellow]"
                        )
                    for idx, mods in to_approve_data.items():
                        for orig, modified, filename in mods:
                            if (
                                filename
                                and orig is not None
                                and modified is not None
                                and not modified.startswith("ERROR:")
                            ):
                                if self.ui:
                                    self.ui.show_diff(filename, orig, modified)

                for i, part in enumerate(tool_calls):
                    needs_approval = (i in to_approve_data) or strict_mode
                    if needs_approval:
                        mods = to_approve_data.get(i, [])

                        can_approve = True
                        # Validate all modifications in the set (especially for batch_job)
                        for _, m, f in mods:
                            if m and str(m).startswith("ERROR:"):
                                if self.ui:
                                    self.ui.show_error(f"Cannot show diff for {f}: {m}")
                                can_approve = False
                                break

                        # Show diffs if not already shown in bulk pre-calculation
                        if len(to_approve_data) <= 1:
                            for o, m, f in mods:
                                if (
                                    f
                                    and o is not None
                                    and m is not None
                                    and not str(m).startswith("ERROR:")
                                ):
                                    if self.ui:
                                        self.ui.show_diff(f, o, m)

                        # Shorten args for display
                        display_args = _shorten_tool_args(part.tool_args)

                        from rich.prompt import Prompt

                        # Add count info to prompt if multiple
                        count_info = (
                            f" ({i + 1}/{len(tool_calls)})"
                            if len(tool_calls) > 1
                            else ""
                        )

                        choice = Prompt.ask(
                            (
                                f"\n[bold yellow]Permission Required[/bold yellow] for tool: [cyan]{part.tool_name}[/cyan]{count_info}\nArgs: {display_args}\nAllow?"
                                if can_approve
                                else f"\n[bold red]Diff Failed[/bold red] for tool: [cyan]{part.tool_name}[/cyan]{count_info}\nArgs: {display_args}\nReject or Explain?"
                            ),
                            choices=["y", "n", "e"] if can_approve else ["n", "e"],
                            default="y" if can_approve else "n",
                        )
                        if choice == "n":
                            result = "User denied this tool call."
                        elif choice == "e":
                            reason = Prompt.ask("Provide an explanation to the model")
                            result = f"User denied this tool call. Reason: {reason}"
                        else:
                            result = execute_tool(
                                part.tool_name,
                                part.tool_args,
                                self.folder_context,
                                self.ui,
                            )
                    else:
                        result = execute_tool(
                            part.tool_name, part.tool_args, self.folder_context, self.ui
                        )

                    if self.ui:
                        self.ui.show_tool_result(result)

                    tool_result_parts.append({
                        "type": "tool_result",
                        "tool_name": part.tool_name,
                        "tool_result": result,
                        "thought_signature": part.thought_signature,
                    })

                tool_result_msg = {"role": "tool", "parts": tool_result_parts}
                self.session_manager.history.append(tool_result_msg)
                self.session_manager.save_history(self.folder_context)

                messages = self._build_messages_from_history(
                    self.session_manager.history[-self.active_context_window :],
                    {"role": "system", "parts": []},
                )[:-1]

            except KeyboardInterrupt:
                if self.ui:
                    self.ui.show_info("\nAgentic loop interrupted by user.")
                self.session_manager.history.append({
                    "role": "tool",
                    "parts": [
                        {
                            "type": "tool_result",
                            "tool_name": "system",
                            "tool_result": "User interrupted execution.",
                        }
                    ],
                })
                self.session_manager.save_history(self.folder_context)
                break
            except Exception as e:
                if self.ui:
                    self.ui.show_error(f"API Error during agentic loop: {e}")
                self.session_manager.save_history(self.folder_context)
                break
