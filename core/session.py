# Session and SessionManager (history state)
import os
import json
import time
import glob
from collections import defaultdict
from datetime import datetime

from core.collation import CollationBuffer
from core.memory import ScratchpadStore, TaskMemoryStore
from core.workspace import FolderContext
from providers.base import LLMProvider, Message, MessagePart, FileReference
from core.tools import TOOLS, execute_tool, get_modifications, COLLATED_TOOLS
from utils.logger import logger
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

def _sanitize_for_log(data):
    """Truncates large data for logging."""
    if isinstance(data, str) and len(data) > 1000:
        return f"{data[:500]}... [TRUNCATED {len(data)-1000} chars] ...{data[-500:]}"
    return data


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
        logger.info(f"Initializing SessionManager (session_name={session_name})")
        self.current_session_name = DEFAULT_SESSION_NAME
        self.history = []  # Stores standardized list of dicts representing messages
        self.provider_config = {}  # Stores { "provider": "...", "model": "..." }
        self.collation_buffer = CollationBuffer()
        self.summary_anchor = 0
        self.folder_context = FolderContext()
        self.task_memory = TaskMemoryStore()
        self.turn_scratchpad = ScratchpadStore()
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
        self.collation_buffer = CollationBuffer()
        self.folder_context = FolderContext()
        self.task_memory = TaskMemoryStore()
        self.turn_scratchpad = ScratchpadStore()
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
                    self.collation_buffer = CollationBuffer.from_dict(data.get("collation_buffer", {}))
                    self.folder_context.from_dict(data.get("folder_context", {}))
                    self.task_memory = TaskMemoryStore.from_dict(data.get("task_memory", {}))
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
        logger.debug(f"Saving history for session: {self.current_session_name}")
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
                "collation_buffer": self.collation_buffer.to_dict(),
                "task_memory": self.task_memory.to_dict(),
                "token_counts": self.token_counts,
            }
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            if self.ui:
                self.ui.show_error(f"Warning: Could not save chat history: {e}")
            logger.error(f"Failed to save history: {e}")

    def switch_session(self, name):
        logger.info(f"Switching to session: {name}")
        self.save_history()
        self._load_session(name)
        if self.ui:
            self.ui.show_info(f"Switched to session: '{name}'")
        self.view_history()

    def new_session(self, name=None, provider_name=None, model_name=None):
        logger.info(f"Creating new session: {name} (provider={provider_name}, model={model_name})")
        self.save_history()
        if not name:
            name = f"chat_{int(time.time())}"
        self.folder_context = FolderContext()
        self.current_session_name = name
        self.collation_buffer = CollationBuffer()
        self.task_memory = TaskMemoryStore()
        self.turn_scratchpad = ScratchpadStore()
        self.history = []
        self.provider_config = {"provider": provider_name, "model": model_name}
        self.token_counts = {"input": 0, "output": 0, "total": 0, "total_cost": 0.0}
        self.variables.clear()
        self.variables.update(DEFAULT_VARIABLES)
        self.save_history()
        if self.ui:
            self.ui.show_info(f"Started new session: '{name}'")

    def list_sessions(self):
        logger.debug("Listing sessions")
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
        logger.info(f"Deleting session: {name}")
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
        logger.info(f"Clearing history for session: {self.current_session_name}")
        self.history = []
        self.token_counts = {"input": 0, "output": 0, "total": 0, "total_cost": 0.0}
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
        logger.info("Initializing Session object")
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
        self._auto_promoted_this_turn = 0

        self.sync_runtime_state()
        if self.folder_context.folders:
            if self.ui:
                self.ui.show_info(
                    f"Restored folder context: {', '.join(self.folder_context.folders)}"
                )
            logger.info(f"Restored folder context: {self.folder_context.folders}")
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
                if self.ui:
                    self.ui.show_info("Upload complete.")
        except Exception as e:
            if self.ui:
                self.ui.show_error(f"Upload failed: {e}")

    def clear_files(self):
        self.staged_files = []
        if self.ui:
            self.ui.show_info("Staged files cleared.")

    def sync_runtime_state(self):
        self.folder_context = self.session_manager.folder_context
        self.collation_buffer = self.session_manager.collation_buffer
        self.task_memory = self.session_manager.task_memory
        self.turn_scratchpad = self.session_manager.turn_scratchpad
        self.variables = self.session_manager.variables


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

    def _message_has_thought_signature(self, msg_dict: dict) -> bool:
        for part in msg_dict.get("parts", []):
            if part.get("thought_signature"):
                return True
        return False

    def _summarize_message_parts(self, msg_dict: dict) -> str:
        role = msg_dict.get("role", "message")
        summaries = []
        for part in msg_dict.get("parts", []):
            p_type = part.get("type")
            if p_type == "text":
                text = str(part.get("text", "")).strip().replace("\n", " ")
                if text:
                    summaries.append(text[:120])
            elif p_type == "tool_call":
                summaries.append(
                    f"tool_call:{part.get('tool_name')} args={_shorten_tool_args(part.get('tool_args', {}))}"
                )
            elif p_type == "tool_result":
                raw_result = part.get("tool_result", "")
                if isinstance(raw_result, dict):
                    result = str(raw_result.get("summary") or raw_result.get("raw", ""))
                else:
                    result = str(raw_result)
                result = result.strip().replace("\n", " ")
                if len(result) > 140:
                    result = f"{result[:137]}..."
                summaries.append(f"tool_result:{part.get('tool_name')} => {result}")
            elif p_type == "file":
                fr = part.get("file_ref", {})
                summaries.append(f"file:{fr.get('display_name', fr.get('uri', 'unknown'))}")

        if not summaries:
            return f"- {role}: [no serializable content]"
        return f"- {role}: " + " | ".join(summaries)

    def _prepare_runtime_history(self, turn_start_index: int | None = None) -> list[dict]:
        recent_history = self.session_manager.history[-self.active_context_window :]
        tool_window = max(0, int(self.variables.get("tool_context_window", 6)))

        if turn_start_index is None:
            return recent_history

        start_in_recent = max(0, turn_start_index - max(0, len(self.session_manager.history) - self.active_context_window))
        prefix = recent_history[:start_in_recent]
        current_turn = recent_history[start_in_recent:]

        tool_messages = [
            msg for msg in current_turn if msg.get("role") in {"assistant", "tool"}
        ]
        if len(tool_messages) <= tool_window:
            return recent_history

        compressible_tool_messages = [
            msg for msg in tool_messages if not self._message_has_thought_signature(msg)
        ]
        if len(compressible_tool_messages) <= tool_window:
            return recent_history

        keep_start = len(compressible_tool_messages) - tool_window
        compressed_tool_count = 0
        summarized_lines = []
        compressed_turn = []

        for msg in current_turn:
            if msg.get("role") in {"assistant", "tool"}:
                if self._message_has_thought_signature(msg):
                    compressed_turn.append(msg)
                    continue
                if compressed_tool_count < keep_start:
                    summarized_lines.append(self._summarize_message_parts(msg))
                    compressed_tool_count += 1
                    continue
                compressed_tool_count += 1
            compressed_turn.append(msg)

        if summarized_lines:
            summary_text = (
                "Compressed prior tool activity for this turn to reduce prompt size.\n"
                + "\n".join(summarized_lines)
            )
            compressed_turn.insert(
                1 if compressed_turn and compressed_turn[0].get("role") == "user" else 0,
                {"role": "system", "parts": [{"type": "text", "text": summary_text}]},
            )

        return prefix + compressed_turn

    def _render_tool_result(self, result) -> str:
        if isinstance(result, dict):
            summary = result.get("summary")
            if summary:
                return str(summary)
            return json.dumps(result, indent=2, sort_keys=True)
        if isinstance(result, list):
            return json.dumps(result, indent=2, sort_keys=True)
        return str(result)

    def _clip_preview(self, text: str, limit: int = 240) -> str:
        text = str(text or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[: limit - 3]}..."

    def _parse_search_results(self, raw_result: str) -> dict:
        matches = []
        file_counts = defaultdict(int)
        for line in str(raw_result).splitlines():
            if " -> " not in line or ":" not in line:
                continue
            path_and_line, snippet = line.split(" -> ", 1)
            try:
                path, line_no = path_and_line.rsplit(":", 1)
                line_no = int(line_no)
            except ValueError:
                continue
            file_counts[path] += 1
            if len(matches) < 8:
                matches.append(
                    {
                        "path": path,
                        "line": line_no,
                        "snippet": self._clip_preview(snippet, 160),
                    }
                )
        return {
            "match_count": sum(file_counts.values()),
            "file_count": len(file_counts),
            "files": sorted(file_counts.keys())[:8],
            "matches": matches,
        }

    def _parse_workspace_details(self, raw_result: str) -> dict:
        folders = []
        tracked_files = []
        section = None
        for line in str(raw_result).splitlines():
            stripped = line.strip()
            if stripped == "Workspace Folders:":
                section = "folders"
                continue
            if stripped == "Tracked Files:":
                section = "files"
                continue
            if stripped.startswith("- "):
                value = stripped[2:]
                if section == "folders":
                    folders.append(value)
                elif section == "files":
                    tracked_files.append(value)
        return {
            "folders": folders,
            "folder_count": len(folders),
            "tracked_file_count": len(tracked_files),
            "tracked_files_preview": tracked_files[:10],
        }

    def _parse_list_dir(self, raw_result: str, path: str) -> dict:
        entries = [line.strip() for line in str(raw_result).splitlines() if line.strip()]
        return {
            "path": path or ".",
            "entry_count": len(entries),
            "entries_preview": entries[:20],
        }

    def _parse_list_agent_tasks(self, raw_result: str) -> dict:
        tasks = []
        for line in str(raw_result).splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                name = stripped[2:].split(":", 1)[0].strip()
                if name:
                    tasks.append(name)
        return {
            "task_count": len(tasks),
            "tasks": tasks[:20],
        }

    def _build_structured_tool_result(self, tool_name: str, tool_args: dict, raw_result):
        raw_text = str(raw_result)
        structured = {
            "tool_name": tool_name,
            "ok": not raw_text.startswith("Error"),
            "summary": self._clip_preview(raw_text, 220),
            "args": _shorten_tool_args(tool_args),
            "raw": raw_text,
            "data": {},
        }

        if tool_name == "read_file":
            structured["data"] = {
                "filename": tool_args.get("filename", ""),
                "char_count": len(raw_text),
                "line_count": len(raw_text.splitlines()),
                "preview": self._clip_preview(raw_text, 240),
            }
        elif tool_name == "get_chunk":
            structured["data"] = {
                "file": tool_args.get("file", ""),
                "start_line": tool_args.get("start_line"),
                "end_line": tool_args.get("end_line"),
                "line_count": len(raw_text.splitlines()),
                "preview": self._clip_preview(raw_text, 240),
            }
        elif tool_name == "search_for_string":
            structured["data"] = {
                "query": tool_args.get("string", ""),
                **self._parse_search_results(raw_text),
            }
        elif tool_name == "list_dir":
            structured["data"] = self._parse_list_dir(raw_text, tool_args.get("path", ""))
        elif tool_name == "get_workspace_details":
            structured["data"] = self._parse_workspace_details(raw_text)
        elif tool_name in {"write_file", "apply_diff"}:
            filename = tool_args.get("filename", "")
            structured["data"] = {
                "filename": filename,
                "changed_file": filename,
            }
        elif tool_name == "list_agent_tasks":
            structured["data"] = self._parse_list_agent_tasks(raw_text)
        elif tool_name == "run_agent_task":
            structured["data"] = {
                "task_name": tool_args.get("task_name", ""),
                "stdout_present": "STDOUT:" in raw_text,
                "stderr_present": "STDERR:" in raw_text,
                "preview": self._clip_preview(raw_text, 260),
            }
        elif tool_name in {"git_status", "git_diff", "git_log", "git_branch"}:
            structured["data"] = {
                "preview": self._clip_preview(raw_text, 260),
            }
        elif tool_name in {
            "save_memory",
            "search_memory",
            "list_memory",
            "save_scratchpad",
            "search_scratchpad",
            "list_scratchpad",
            "clear_scratchpad",
            "flush",
        }:
            structured["data"] = {"preview": self._clip_preview(raw_text, 220)}

        return structured

    def _auto_promote_memory_candidates(self, structured_result: dict) -> list[dict]:
        if not structured_result.get("ok", False):
            return []

        tool_name = structured_result.get("tool_name")
        data = structured_result.get("data", {})
        candidates = []

        if tool_name == "search_for_string" and data.get("files"):
            candidates.append(
                {
                    "content": (
                        f"Search '{data.get('query', '')}' matched {data.get('match_count', 0)} lines "
                        f"across {data.get('file_count', 0)} files; key files: {', '.join(data.get('files', [])[:5])}."
                    ),
                    "tags": ["search", "files"],
                    "source": tool_name,
                }
            )
        elif tool_name in {"write_file", "apply_diff"} and data.get("changed_file"):
            candidates.append(
                {
                    "content": f"Modified file: {data['changed_file']}.",
                    "tags": ["file-change"],
                    "source": tool_name,
                }
            )
        elif tool_name == "list_agent_tasks" and data.get("tasks"):
            candidates.append(
                {
                    "content": f"Available agent tasks: {', '.join(data.get('tasks', [])[:8])}.",
                    "tags": ["agent-task"],
                    "source": tool_name,
                }
            )
        elif tool_name == "get_workspace_details" and data.get("folders"):
            candidates.append(
                {
                    "content": (
                        f"Workspace roots: {', '.join(data.get('folders', [])[:3])}; "
                        f"tracked files: {data.get('tracked_file_count', 0)}."
                    ),
                    "tags": ["workspace"],
                    "source": tool_name,
                }
            )
        elif tool_name == "run_agent_task" and data.get("task_name"):
            candidates.append(
                {
                    "content": f"Agent task '{data['task_name']}' completed with preview: {data.get('preview', '')}",
                    "tags": ["agent-task-run"],
                    "source": tool_name,
                }
            )

        return candidates

    def _maybe_auto_promote_memory(self, structured_result: dict) -> list[str]:
        if not self.variables.get("auto_promote_memory", True):
            return []

        max_promotions = max(0, int(self.variables.get("auto_promote_max_per_turn", 8)))
        if self._auto_promoted_this_turn >= max_promotions:
            return []

        promoted = []
        for candidate in self._auto_promote_memory_candidates(structured_result):
            if self._auto_promoted_this_turn >= max_promotions:
                break
            entry = self.task_memory.save(
                candidate["content"],
                tags=candidate.get("tags", []),
                source=candidate.get("source", structured_result.get("tool_name", "")),
            )
            self._auto_promoted_this_turn += 1
            promoted.append(f"auto-promoted memory #{entry.id}")
        return promoted

    def _execute_tool_with_memory(self, tool_name: str, tool_args: dict):
        if tool_name == "save_memory":
            entry = self.task_memory.save(
                tool_args.get("content", ""),
                tags=tool_args.get("tags", []),
                source=tool_args.get("source", ""),
            )
            return f"Saved memory #{entry.id} with tags={entry.tags}."

        if tool_name == "save_scratchpad":
            entry = self.turn_scratchpad.save(
                tool_args.get("content", ""),
                tags=tool_args.get("tags", []),
                source=tool_args.get("source", ""),
            )
            return f"Saved scratchpad note #{entry.id} with tags={entry.tags}."

        if tool_name == "search_memory":
            entries = self.task_memory.search(
                tool_args.get("query", ""),
                limit=int(tool_args.get("limit", 5) or 5),
            )
            return self.task_memory.format_results(entries)

        if tool_name == "search_scratchpad":
            entries = self.turn_scratchpad.search(
                tool_args.get("query", ""),
                limit=int(tool_args.get("limit", 5) or 5),
            )
            return self.turn_scratchpad.format_results(entries)

        if tool_name == "list_memory":
            entries = self.task_memory.list_entries(
                limit=int(tool_args.get("limit", 10) or 10)
            )
            return self.task_memory.format_results(entries)

        if tool_name == "list_scratchpad":
            entries = self.turn_scratchpad.list_entries(
                limit=int(tool_args.get("limit", 10) or 10)
            )
            return self.turn_scratchpad.format_results(entries)

        if tool_name == "clear_scratchpad":
            self.turn_scratchpad.clear()
            return "Turn scratchpad cleared."

        if tool_name == "batch_job":
            commands = tool_args.get("commands", [])
            if not isinstance(commands, list):
                return "Error: 'commands' must be a list."
            lines = ["--- Batch Job Results ---"]
            for index, command in enumerate(commands, 1):
                if not isinstance(command, dict):
                    lines.append(f"[{index}] Error: Invalid command entry.")
                    continue
                nested_name = command.get("tool_name", "")
                nested_args = command.get("tool_args", {})
                if nested_name == "batch_job":
                    lines.append(f"[{index}] Error: nested batch_job not allowed.")
                    continue
                nested_result = self._execute_tool_with_memory(nested_name, nested_args)
                lines.append(f"[{index}] Tool: {nested_name}\n{nested_result}")
            return "\n\n".join(lines)

        return execute_tool(
            tool_name,
            tool_args,
            self.folder_context,
            self.ui,
            self.variables,
        )

    def _prompt_tool_choice(self, prompt_text: str, choices: list[str], default: str) -> str:
        if self.ui and hasattr(self.ui, "prompt_choices"):
            return self.ui.prompt_choices(prompt_text, choices=choices, default=default)
        return default

    def _confirm_retry(self) -> bool:
        if self.ui and hasattr(self.ui, "confirm"):
            return self.ui.confirm(
                "An error occurred during the LLM call. Would you like to retry?",
                default=True,
            )
        return False

    def send_message(self, text):
        logger.info(f"Sending message: {text[:100]}...")
        self.sync_runtime_state()
        if self.variables.get("scratchpad_enabled", True):
            self.turn_scratchpad.max_entries = max(
                1, int(self.variables.get("scratchpad_max_entries", self.turn_scratchpad.max_entries))
            )
            self.turn_scratchpad.clear()
        self._auto_promoted_this_turn = 0
        
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
                logger.debug(f"Using agent_mode={self.variables.get('agent_mode', 'default')}")

                if self.ui:
                    with self.ui.show_status(
                        "Scanning monitored folders for changes..."
                    ):
                        folder_initial_xml = (
                            self.folder_context.get_initial_context_xml()
                        )
                        folder_diff_xml = self.folder_context.get_context_diff_xml()
                        workspace_context = f"{folder_initial_xml}\n\n{folder_diff_xml}"

        base_system_prompt = self.system_instruction
        if workspace_context:
            base_system_prompt += f"\n\n{workspace_context}"

        recent_history = self.session_manager.history[-self.active_context_window :]
        messages = self._build_messages_from_history(recent_history, new_user_message)

        self.session_manager.history.append(new_user_message)
        self.session_manager.save_history()
        self.staged_files = []
        turn_start_index = len(self.session_manager.history) - 1

        max_iterations = self.variables.get("max_iterations", 50)
        iteration = 0
        active_tools = [t for t in TOOLS if t.name not in self.disabled_tools]

        total_in = 0
        total_out = 0
        total_cost = 0.0

        logger.info(f"Starting agentic loop (max_iterations={max_iterations})")

        while iteration < max_iterations:
            iteration += 1
            logger.debug(f"Agentic loop iteration {iteration}/{max_iterations}")

            try:
                dynamic_system_prompt = base_system_prompt
                if self.variables.get("memory_enabled", True):
                    self.task_memory.max_entries = max(
                        1, int(self.variables.get("memory_max_entries", self.task_memory.max_entries))
                    )
                    memory_summary = self.task_memory.render_summary(
                        limit=int(self.variables.get("memory_summary_limit", 8))
                    )
                    if memory_summary:
                        dynamic_system_prompt += (
                            "\n\nPersisted working memory is available below. Prefer using it before"
                            " re-reading large tool outputs.\n"
                            f"{memory_summary}"
                        )
                if self.variables.get("scratchpad_enabled", True):
                    scratchpad_summary = self.turn_scratchpad.render_summary(limit=8)
                    if scratchpad_summary:
                        dynamic_system_prompt += f"\n\n{scratchpad_summary}"

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

                logger.debug(f"Provider response received. Tokens: In {response.input_tokens}, Out {response.output_tokens}")

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
                        logger.debug(f"Assistant text: {part.text[:200]}...")
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
                        if self.ui:
                            self.ui.show_info(
                                f"🔨 Running tool: {part.tool_name}({_shorten_tool_args(part.tool_args)})"
                            )
                        logger.info(f"Tool call: {part.tool_name} with args {part.tool_args}")

                if ai_parts_archive:
                    self.session_manager.history.append(
                        {
                            "role": "assistant",
                            "parts": ai_parts_archive,
                        }
                    )

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
                        logger.warning("Assistant provided empty response. Nudging.")

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

                    logger.info("Agentic loop finished (no tool calls).")

                    if self.variables.get("compact_history", False):
                        if self.ui:
                            self.ui.show_info(
                                "[dim]Compacting turn history (removing tool metadata)...[/dim]"
                            )
                            self.session_manager.compact_completed_turn()
                        logger.debug("History compacted.")

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

                        result = None
                        can_approve = True
                        error_msg = None

                        # Validate all modifications in the set (especially for batch_job)
                        for _, m, f in mods:
                            if m and str(m).startswith("ERROR:"):
                                if "malformed patch" in str(
                                    m
                                ).lower() or "patch: ****" in str(m):
                                    error_msg = m
                                    can_approve = False
                                    break
                                if self.ui:
                                    self.ui.show_error(f"Cannot show diff for {f}: {m}")
                                can_approve = False
                                logger.error(f"Diff error for {f}: {m}")
                                break

                        if not can_approve and error_msg:
                            if self.ui:
                                self.ui.show_info(
                                    f"  [yellow]Auto-retrying malformed patch for {part.tool_name}...[/yellow]"
                                )
                            result = f"Error: Malformed patch detected. Please ensure your diff is correctly formatted. Check hunk headers and context.\n{error_msg}"
                            logger.warning(f"Malformed patch detected for {part.tool_name}: {error_msg}")
                            # Fall through to skip Prompt.ask since result is now set

                        # Show diffs if not already shown in bulk pre-calculation
                        if result is None and len(to_approve_data) <= 1:
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

                        # Add count info to prompt if multiple
                        count_info = (
                            f" ({i + 1}/{len(tool_calls)})"
                            if len(tool_calls) > 1
                            else ""
                        )

                        if result is None:
                            choice = self._prompt_tool_choice(
                                (
                                    f"\n[bold yellow]Permission Required[/bold yellow] for tool: [cyan]{part.tool_name}[/cyan]{count_info}\nArgs: {display_args}\nAllow?"
                                    if can_approve
                                    else f"\n[bold red]Diff Failed[/bold red] for tool: [cyan]{part.tool_name}[/cyan]{count_info}\nArgs: {display_args}\nReject or Explain?"
                                ),
                                ["y", "n", "e"] if can_approve else ["n", "e"],
                                "y" if can_approve else "n",
                            )
                            if choice == "n":
                                result = "User denied this tool call."
                                logger.info(f"Tool call {part.tool_name} denied by user.")
                            elif choice == "e":
                                reason = self.ui.prompt("Provide an explanation to the model")
                                result = f"User denied this tool call. Reason: {reason}"
                                logger.info(f"Tool call {part.tool_name} denied by user with explanation: {reason}")
                            else:
                                result = self._execute_tool_with_memory(
                                    part.tool_name,
                                    part.tool_args,
                                )
                    else:
                        result = self._execute_tool_with_memory(
                            part.tool_name,
                            part.tool_args,
                        )

                    source_result = result
                    raw_result = source_result
                    logger.debug(f"Tool result ({part.tool_name}): {_sanitize_for_log(raw_result)}")

                    # --- Collation Logic ---
                    is_flush = part.tool_name == "flush"
                    should_collate = (
                        part.tool_name in COLLATED_TOOLS 
                        and self.variables.get("collation_enabled", True)
                    )

                    if is_flush:
                        collated_data = self.collation_buffer.flush()
                        if not collated_data:
                            raw_result = "No data in collation buffer to flush."
                        else:
                            raw_result = "--- Flushed Context ---\n" + "\n\n".join(collated_data)
                        if self.ui:
                            self.ui.show_info(f"  [Flushed {len(collated_data)} items from buffer]")
                    elif should_collate:
                        # Don't collate if there was an error
                        if raw_result and not str(raw_result).startswith("Error"):
                            self.collation_buffer.add(part.tool_name, part.tool_args, raw_result)
                            count = len(self.collation_buffer.entries)
                            raw_result = (
                                f"Stored '{part.tool_name}' result in collation buffer. "
                                f"{count} item(s) currently pending. "
                                "Continue gathering or call 'flush' when ready to receive all context."
                            )
                            if self.ui:
                                self.ui.show_info(f"  [Collated: {part.tool_name}]")
                        else:
                            # If it's an error, don't collate it, let the model see the error immediately
                            if self.ui:
                                self.ui.show_tool_result(self._render_tool_result(raw_result))
                    else:
                        if self.ui:
                            self.ui.show_tool_result(self._render_tool_result(raw_result))

                    # --- End Collation Logic ---
                    if self.variables.get("structured_tool_results", True):
                        promotion_result = None
                        if raw_result != source_result:
                            source_text = str(source_result)
                            result = {
                                "tool_name": part.tool_name,
                                "ok": not source_text.startswith("Error"),
                                "summary": self._clip_preview(raw_result, 220),
                                "args": _shorten_tool_args(part.tool_args),
                                "raw": str(raw_result),
                                "data": {
                                    "collated": True,
                                    "pending_items": len(self.collation_buffer.entries),
                                    "source_char_count": len(source_text),
                                    "source_line_count": len(source_text.splitlines()),
                                },
                            }
                            promotion_result = self._build_structured_tool_result(
                                part.tool_name,
                                part.tool_args,
                                source_result,
                            )
                        else:
                            result = self._build_structured_tool_result(
                                part.tool_name,
                                part.tool_args,
                                source_result,
                            )
                            promotion_result = result
                        promotions = self._maybe_auto_promote_memory(promotion_result)
                        if promotions:
                            promo_text = "; ".join(promotions)
                            result["summary"] = f"{result.get('summary', '')} [{promo_text}]".strip()
                    else:
                        result = raw_result

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
                    self._prepare_runtime_history(turn_start_index),
                    {"role": "system", "parts": []},
                )[:-1]

            except KeyboardInterrupt:
                if self.ui:
                    self.ui.show_info("\nAgentic loop interrupted by user.")
                logger.warning("Agentic loop interrupted by user.")
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
                if self.ui:
                    self.ui.show_error(f"API Error during agentic loop: {e}")
                logger.error(f"Error in agentic loop: {e}", exc_info=True)

                # Failsafe for retry
                if self._confirm_retry():
                    iteration -= 1  # Decrement so the next loop run tries the same step
                    continue

                self.session_manager.save_history(self.folder_context)
                break
