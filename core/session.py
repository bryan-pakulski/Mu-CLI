# Session and SessionManager (history state)
import os
import json
import time
import glob
import re
import random
import shutil
import traceback
from copy import deepcopy
from collections import defaultdict
from datetime import datetime

from core.approval import build_approval_prompt, collect_approval_plans, ApprovalPlan
from core.collation import CollationBuffer
from core.feature_mode import refresh_and_persist_feature_plan, summarize_feature_plan
from core.memory import ScratchpadStore, TaskMemoryStore
from core.retrieval import SemanticCodeIndex
from core.workspace import FolderContext
from providers.base import LLMProvider, Message, MessagePart, FileReference
from core.tools import (
    TOOLS,
    COLLATED_TOOLS,
    execute_tool,
    infer_tool_error_code,
)
from utils.logger import logger
from utils.helpers import get_safe_mime_type, display_image_in_terminal
from utils.runtime_metrics import build_live_status_line
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
    if not isinstance(args, dict):
        return {"_raw_args": str(args)}
    shortened = args.copy()
    for key in ["content", "diff"]:
        if (
            key in shortened
            and isinstance(shortened[key], str)
            and len(shortened[key]) > 100
        ):
            shortened[key] = f"({len(shortened[key])} chars)"
    return shortened


def _safe_feature_path_prefix(path: str) -> str:
    normalized = os.path.abspath(path)
    return normalized if normalized.endswith(os.sep) else f"{normalized}{os.sep}"


def _slugify_feature_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "feature"


class SessionManager:
    def __init__(self, ui=None, session_name=None):
        self.ui = ui
        logger.info(f"Initializing SessionManager (session_name={session_name})")
        self.current_session_name = DEFAULT_SESSION_NAME
        self.history = []  # Stores standardized list of dicts representing messages
        self.conversation_summary = ""
        self.provider_config = {}  # Stores { "provider": "...", "model": "..." }
        self.collation_buffer = CollationBuffer()
        self.summary_anchor = 0
        self.folder_context = FolderContext()
        self.task_memory = TaskMemoryStore()
        self.turn_scratchpad = ScratchpadStore()
        self.token_counts = {"input": 0, "output": 0, "total": 0, "total_cost": 0.0}
        self.feature_state = None
        self.feature_registry = {}
        self.active_feature_id = None
        self.variables = DEFAULT_VARIABLES.copy()

        if session_name:
            self._load_session(session_name)
        else:
            self._load_session(DEFAULT_SESSION_NAME)

    def _get_filepath(self, name):
        return os.path.join(self._get_session_dir(name), "session.json")

    def _get_session_dir(self, name):
        return os.path.join(HISTORY_DIR, "sessions", name)

    def _load_session(self, name):
        filepath = self._get_filepath(name)
        legacy_filepath = os.path.join(HISTORY_DIR, f"{name}.json")
        self.current_session_name = name
        self.history = []
        self.conversation_summary = ""
        self.summary_anchor = 0
        self.provider_config = {}
        self.collation_buffer = CollationBuffer()
        self.folder_context = FolderContext()
        self.task_memory = TaskMemoryStore()
        self.turn_scratchpad = ScratchpadStore()
        self.variables.clear()
        self.token_counts = {"input": 0, "output": 0, "total": 0, "total_cost": 0.0}
        self.feature_state = None
        self.feature_registry = {}
        self.active_feature_id = None
        self.variables.update(DEFAULT_VARIABLES)

        data = self.read_session_data(name)
        if data is not None:
            try:
                if isinstance(data, list):
                    self.history = data
                elif isinstance(data, dict):
                    self.history = data.get("history", [])
                    self.conversation_summary = str(
                        data.get("conversation_summary", "") or ""
                    )
                    self.summary_anchor = data.get("summary_anchor", 0)
                    self.provider_config = data.get("provider_config", {})
                    self.collation_buffer = CollationBuffer.from_dict(
                        data.get("collation_buffer", {})
                    )
                    self.folder_context.from_dict(data.get("folder_context", {}))
                    self.task_memory = TaskMemoryStore.from_dict(
                        data.get("task_memory", {})
                    )
                    self.turn_scratchpad = ScratchpadStore.from_dict(
                        data.get("turn_scratchpad", {})
                    )
                    self.token_counts = data.get(
                        "token_counts",
                        {"input": 0, "output": 0, "total": 0, "total_cost": 0.0},
                    )
                    feature_state = data.get("feature_state")
                    if isinstance(feature_state, dict):
                        self.feature_state = feature_state
                    self.feature_registry = {
                        str(key): value
                        for key, value in (
                            data.get("feature_registry", {}) or {}
                        ).items()
                        if isinstance(value, dict)
                    }
                    self.active_feature_id = data.get("active_feature_id")
                    if (
                        self.feature_state is None
                        and self.active_feature_id in self.feature_registry
                    ):
                        self.feature_state = deepcopy(
                            self.feature_registry[self.active_feature_id]
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

    def read_session_data(self, name):
        filepath = self._get_filepath(name)
        legacy_filepath = os.path.join(HISTORY_DIR, f"{name}.json")
        source_filepath = filepath if os.path.exists(filepath) else legacy_filepath
        if not os.path.exists(source_filepath):
            return None
        try:
            with open(source_filepath, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def get_session_history(self, name):
        data = self.read_session_data(name)
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("history", [])
        return []

    def save_history(self, folder_context_obj=None):
        logger.debug(f"Saving history for session: {self.current_session_name}")
        filepath = self._get_filepath(self.current_session_name)
        if folder_context_obj:
            self.folder_context = folder_context_obj

        try:
            os.makedirs(self._get_session_dir(self.current_session_name), exist_ok=True)
            data = {
                "history": self.history,
                "conversation_summary": self.conversation_summary,
                "summary_anchor": self.summary_anchor,
                "provider_config": self.provider_config,
                "folder_context": self.folder_context.to_dict(),
                "variables": self.variables,
                "collation_buffer": self.collation_buffer.to_dict(),
                "task_memory": self.task_memory.to_dict(),
                "turn_scratchpad": self.turn_scratchpad.to_dict(),
                "token_counts": self.token_counts,
                "feature_state": self.feature_state,
                "feature_registry": self.feature_registry,
                "active_feature_id": self.active_feature_id,
            }
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            if self.ui:
                self.ui.show_error(f"Warning: Could not save chat history: {e}")
            logger.error(f"Failed to save history: {e}")

    def get_feature_state(self):
        return (
            deepcopy(self.feature_state)
            if isinstance(self.feature_state, dict)
            else None
        )

    def get_feature_metadata_root(self) -> str:
        return os.path.join(self._get_session_dir(self.current_session_name), "features")

    def get_feature_metadata_path(self, feature_id: str) -> str:
        return os.path.join(
            self.get_feature_metadata_root(),
            f"{_slugify_feature_id(feature_id)}.json",
        )

    def get_feature_metadata_index(self) -> dict[str, str]:
        index = {}
        for feature in self.feature_registry.values():
            directory = str(feature.get("directory", "") or "").strip()
            metadata_path = str(feature.get("metadata_path", "") or "").strip()
            if directory and metadata_path:
                index[directory] = metadata_path
        return index

    def list_features(self) -> list[dict]:
        features = [deepcopy(feature) for feature in self.feature_registry.values()]
        features.sort(
            key=lambda feature: float(feature.get("updated_at", 0) or 0), reverse=True
        )
        return features

    def get_feature(self, feature_id: str | None = None) -> dict | None:
        resolved_feature_id = feature_id or self.active_feature_id
        if not resolved_feature_id:
            return None
        feature = self.feature_registry.get(str(resolved_feature_id))
        return deepcopy(feature) if isinstance(feature, dict) else None

    def upsert_feature(self, feature: dict | None) -> dict | None:
        if not isinstance(feature, dict):
            return None
        feature_id = str(
            feature.get("feature_id")
            or feature.get("id")
            or feature.get("feature_name")
            or ""
        ).strip()
        if not feature_id:
            return None
        feature_id = _slugify_feature_id(feature_id)
        record = deepcopy(feature)
        record["feature_id"] = feature_id
        record["updated_at"] = float(
            record.get("updated_at", time.time()) or time.time()
        )
        self.feature_registry[feature_id] = record
        return deepcopy(record)

    def activate_feature(self, feature_id: str) -> dict | None:
        record = self.get_feature(feature_id)
        if not record:
            return None
        self.active_feature_id = record["feature_id"]
        self.feature_state = deepcopy(record)
        self.save_history()
        return deepcopy(record)

    def delete_feature(self, feature_id: str) -> dict | None:
        resolved_feature_id = _slugify_feature_id(feature_id)
        record = self.feature_registry.pop(resolved_feature_id, None)
        if not isinstance(record, dict):
            return None
        metadata_path = str(record.get("metadata_path", "") or "").strip()
        if metadata_path and os.path.exists(metadata_path):
            os.remove(metadata_path)
        if self.active_feature_id == resolved_feature_id:
            self.active_feature_id = None
            self.feature_state = None
        self.save_history()
        return deepcopy(record)

    def create_feature_record(
        self,
        feature_name: str,
        *,
        directory: str,
        feature_request: str = "",
    ) -> dict:
        feature_id = self.allocate_feature_id(feature_name)
        metadata_path = self.get_feature_metadata_path(feature_id)
        os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
        record = {
            "type": "feature",
            "status": "draft",
            "feature_id": feature_id,
            "feature_name": feature_name.strip() or feature_id,
            "directory": directory,
            "metadata_path": metadata_path,
            "feature_plan": {
                "feature_id": feature_id,
                "feature_name": feature_name.strip() or feature_id,
                "feature_request": feature_request.strip()
                or feature_name.strip()
                or feature_id,
                "directory": directory,
                "metadata_path": metadata_path,
                "approved": False,
                "review_status": "pending",
                "review_notes": "",
                "overall_status": "not_started",
                "phases_completed": False,
                "phase_count": 0,
                "phases": [],
                "next_phase": None,
            },
            "blocker": None,
            "updated_at": time.time(),
        }
        with open(metadata_path, "w", encoding="utf-8") as handle:
            json.dump(record["feature_plan"], handle, indent=2)
        self.upsert_feature(record)
        self.active_feature_id = feature_id
        self.feature_state = deepcopy(record)
        self.save_history()
        return deepcopy(record)

    def allocate_feature_id(self, requested_id: str) -> str:
        base = _slugify_feature_id(requested_id)
        if base not in self.feature_registry:
            return base
        suffix = 2
        while True:
            candidate = f"{base}_{suffix}"
            if candidate not in self.feature_registry:
                return candidate
            suffix += 1

    def set_feature_state(self, state: dict | None, folder_context_obj=None):
        self.feature_state = deepcopy(state) if isinstance(state, dict) else None
        if isinstance(self.feature_state, dict):
            record = self.upsert_feature(self.feature_state)
            if record:
                self.active_feature_id = record["feature_id"]
        self.save_history(folder_context_obj)

    def clear_feature_state(self, folder_context_obj=None):
        self.feature_state = None
        self.active_feature_id = None
        self.save_history(folder_context_obj)

    def switch_session(self, name):
        logger.info(f"Switching to session: {name}")
        self.save_history()
        self._load_session(name)
        if self.ui:
            self.ui.show_info(f"Switched to session: '{name}'")
        self.view_history()

    def new_session(self, name=None, provider_name=None, model_name=None):
        logger.info(
            f"Creating new session: {name} (provider={provider_name}, model={model_name})"
        )
        self.save_history()
        if not name:
            name = f"chat_{int(time.time())}"
        self.folder_context = FolderContext()
        self.current_session_name = name
        self.collation_buffer = CollationBuffer()
        self.task_memory = TaskMemoryStore()
        self.turn_scratchpad = ScratchpadStore()
        self.feature_state = None
        self.feature_registry = {}
        self.active_feature_id = None
        self.conversation_summary = ""
        self.summary_anchor = 0
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

        files = glob.glob(os.path.join(HISTORY_DIR, "sessions", "*", "session.json"))
        if self.ui:
            # We might want a specific UI method for listing sessions
            self.ui.show_info("\n=== Available Conversations ===")
            for f in files:
                name = os.path.basename(os.path.dirname(f))
                indicator = "*" if name == self.current_session_name else " "
                mod_time = datetime.fromtimestamp(os.path.getmtime(f)).strftime(
                    "%Y-%m-%d %H:%M"
                )
                self.ui.show_info(f" {indicator} {name:<20} ({mod_time})")

    def get_session_list(self):
        files = glob.glob(os.path.join(HISTORY_DIR, "sessions", "*", "session.json"))
        sessions = []
        for f in files:
            sessions.append(os.path.basename(os.path.dirname(f)))
        return sorted(sessions)

    def delete_session(self, name):
        logger.info(f"Deleting session: {name}")
        if name == self.current_session_name:
            if self.ui:
                self.ui.show_error("Cannot delete active session.")
            return

        session_dir = self._get_session_dir(name)
        if os.path.exists(session_dir):
            shutil.rmtree(session_dir)
            if self.ui:
                self.ui.show_info(f"Deleted session: '{name}'")
        else:
            if self.ui:
                self.ui.show_error(f"Session '{name}' not found.")

    def rename_session(self, old_name: str, new_name: str) -> bool:
        old_name = str(old_name or "").strip()
        new_name = str(new_name or "").strip()
        if not old_name or not new_name:
            raise ValueError("Both old_name and new_name are required.")
        if old_name == new_name:
            return True

        old_dir = self._get_session_dir(old_name)
        new_dir = self._get_session_dir(new_name)
        if not os.path.exists(old_dir):
            raise FileNotFoundError(f"Session '{old_name}' not found.")
        if os.path.exists(new_dir):
            raise FileExistsError(f"Session '{new_name}' already exists.")

        os.rename(old_dir, new_dir)
        if self.current_session_name == old_name:
            self.current_session_name = new_name
            self.save_history()
        if self.ui:
            self.ui.show_info(f"Renamed session '{old_name}' to '{new_name}'.")
        return True

    def clear_current_history(self):
        logger.info(f"Clearing history for session: {self.current_session_name}")
        self.history = []
        self.conversation_summary = ""
        self.summary_anchor = 0
        self.token_counts = {"input": 0, "output": 0, "total": 0, "total_cost": 0.0}
        self.save_history()
        if self.ui:
            self.ui.show_info("Current chat history cleared.")

    def reset_current_session_state(self):
        logger.info(f"Resetting session state for session: {self.current_session_name}")
        self.history = []
        self.conversation_summary = ""
        self.summary_anchor = 0
        self.folder_context = FolderContext()
        self.collation_buffer = CollationBuffer()
        self.task_memory = TaskMemoryStore()
        self.turn_scratchpad = ScratchpadStore()
        self.token_counts = {"input": 0, "output": 0, "total": 0, "total_cost": 0.0}
        self.feature_state = None
        self.feature_registry = {}
        self.active_feature_id = None

        feature_root = self.get_feature_metadata_root()
        if os.path.isdir(feature_root):
            for entry in glob.glob(os.path.join(feature_root, "*.json")):
                os.remove(entry)

        self.save_history()

    def _summarize_history_batch(self, entries: list[dict]) -> str:
        lines = []
        for entry in entries:
            lines.append(self._summarize_history_message(entry))
        return "\n".join(line for line in lines if line)

    def _summarize_history_message(self, entry: dict) -> str:
        role = str(entry.get("role", "message"))
        parts = []
        for part in entry.get("parts", []):
            part_type = part.get("type")
            if part_type == "text":
                text = str(part.get("text", "")).strip().replace("\n", " ")
                if text:
                    parts.append(text[:140])
            elif part_type == "tool_call":
                parts.append(
                    f"tool_call:{part.get('tool_name')} args={_shorten_tool_args(part.get('tool_args', {}))}"
                )
            elif part_type == "tool_result":
                result = str(part.get("tool_result", "")).strip().replace("\n", " ")
                if len(result) > 140:
                    result = f"{result[:137]}..."
                if result:
                    parts.append(
                        f"tool_result:{part.get('tool_name', 'tool')} => {result}"
                    )
            elif part_type == "file":
                file_ref = part.get("file_ref", {})
                parts.append(
                    f"file:{file_ref.get('display_name') or file_ref.get('uri') or 'unknown'}"
                )

        if not parts:
            return f"- {role}: [no serializable content]"
        return f"- {role}: " + " | ".join(parts)

    def _clip_conversation_summary(self, limit: int = 4_000) -> None:
        if len(self.conversation_summary) <= limit:
            return
        clipped = self.conversation_summary[-limit:].lstrip()
        newline_index = clipped.find("\n")
        if newline_index > 0:
            clipped = clipped[newline_index + 1 :]
        self.conversation_summary = (
            f"[conversation_summary_truncated_to_last_{limit}_chars]\n{clipped}"
        ).strip()

    @staticmethod
    def _estimate_tokens_from_text(text: str) -> int:
        raw = str(text or "")
        if not raw:
            return 0
        return max(1, int(len(raw) / 4))

    def _estimate_message_tokens(self, message: dict) -> int:
        role = str(message.get("role", "") or "")
        total = 3 + self._estimate_tokens_from_text(role)
        for part in message.get("parts", []):
            part_type = str(part.get("type", "") or "")
            total += self._estimate_tokens_from_text(part_type)
            if part_type == "text":
                total += self._estimate_tokens_from_text(part.get("text", ""))
            elif part_type == "tool_call":
                total += self._estimate_tokens_from_text(part.get("tool_name", ""))
                total += self._estimate_tokens_from_text(
                    json.dumps(part.get("tool_args", {}), default=str)
                )
            elif part_type == "tool_result":
                total += self._estimate_tokens_from_text(part.get("tool_name", ""))
                total += self._estimate_tokens_from_text(
                    json.dumps(part.get("tool_result", ""), default=str)
                )
            elif part_type == "file":
                file_ref = part.get("file_ref", {}) or {}
                total += self._estimate_tokens_from_text(
                    file_ref.get("display_name") or file_ref.get("uri") or ""
                )
        return total

    def estimate_runtime_history_tokens(self, start_index: int | None = None) -> int:
        start = self.summary_anchor if start_index is None else max(0, int(start_index))
        return sum(
            self._estimate_message_tokens(message) for message in self.history[start:]
        )

    def roll_history_summary(self, keep_recent: int) -> bool:
        keep_recent = max(1, int(keep_recent or 1))
        if self.summary_anchor > len(self.history):
            self.summary_anchor = 0
        unsummarized_count = len(self.history) - self.summary_anchor
        if unsummarized_count <= keep_recent:
            return False

        target_anchor = len(self.history) - keep_recent
        for idx in range(target_anchor, len(self.history)):
            if self.history[idx].get("role") == "user":
                target_anchor = idx
                break

        if target_anchor <= self.summary_anchor:
            return False

        summary_batch = self._summarize_history_batch(
            self.history[self.summary_anchor : target_anchor]
        )
        if not summary_batch:
            self.summary_anchor = target_anchor
            return True

        header = (
            f"### Summarized conversation through message {target_anchor}\n"
            if not self.conversation_summary
            else f"\n### Summarized conversation through message {target_anchor}\n"
        )
        self.conversation_summary = (
            f"{self.conversation_summary}{header}{summary_batch}".strip()
        )
        self._clip_conversation_summary()
        self.summary_anchor = target_anchor
        return True

    def roll_history_summary_to_token_budget(
        self,
        token_budget: int,
        *,
        keep_recent: int = 12,
        max_passes: int = 8,
    ) -> bool:
        token_budget = max(1, int(token_budget or 1))
        changed = False
        for _ in range(max(1, int(max_passes or 1))):
            if self.estimate_runtime_history_tokens() <= token_budget:
                break
            if self.roll_history_summary(keep_recent=keep_recent):
                changed = True
                continue
            if self._degrade_oldest_runtime_payload():
                changed = True
                continue
            break
        return changed

    def _degrade_oldest_runtime_payload(self, max_chars: int = 4000) -> bool:
        """Fallback budget guard: clip oldest large unsummarized payloads."""
        if self.summary_anchor > len(self.history):
            self.summary_anchor = 0
        for message in self.history[self.summary_anchor :]:
            parts = message.get("parts", []) or []
            for part in parts:
                p_type = part.get("type")
                if p_type == "text":
                    value = str(part.get("text", "") or "")
                    if len(value) > max_chars:
                        part["text"] = (
                            value[:max_chars].rstrip()
                            + f"\n[truncated_to_{max_chars}_chars_for_context_budget]"
                        )
                        return True
                elif p_type == "tool_result":
                    raw = part.get("tool_result", "")
                    serialized = (
                        json.dumps(raw, default=str)
                        if not isinstance(raw, str)
                        else raw
                    )
                    if len(serialized) > max_chars:
                        clipped = (
                            serialized[:max_chars].rstrip()
                            + f"\n[truncated_to_{max_chars}_chars_for_context_budget]"
                        )
                        part["tool_result"] = clipped
                        return True
        return False

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
        self.summary_anchor = min(self.summary_anchor, len(self.history))


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
        self.staged_files = []  # list of dicts
        self.disabled_tools = []  # list of tool names strings
        self.retrieval_index = SemanticCodeIndex()
        self._pending_retrieved_context = ""

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
        self.feature_state = self.session_manager.get_feature_state()
        self.variables = self.session_manager.variables
        setattr(
            self.folder_context,
            "feature_metadata_dir",
            self.session_manager.get_feature_metadata_root(),
        )
        setattr(
            self.folder_context,
            "feature_metadata_index",
            self.session_manager.get_feature_metadata_index(),
        )

    def _derive_feature_state_status(self, feature_plan: dict | None) -> str:
        if not isinstance(feature_plan, dict):
            return "running"
        if not feature_plan.get("approved", False):
            return "awaiting_approval"
        if feature_plan.get("review_status") == "completed":
            return "completed"
        if (
            feature_plan.get("phases_completed")
            and feature_plan.get("next_phase") is None
        ):
            return "review"
        return "running"

    def _set_feature_state(
        self,
        *,
        feature_plan: dict | None = None,
        status: str | None = None,
        blocker: dict | None = None,
    ):
        current = self.session_manager.get_feature_state() or {}
        current_plan = current.get("feature_plan")
        plan_summary = feature_plan if isinstance(feature_plan, dict) else current_plan
        next_phase = (
            plan_summary.get("next_phase")
            if isinstance(plan_summary, dict)
            else current.get("next_phase")
        )
        state = {
            "type": "feature",
            "status": status or self._derive_feature_state_status(plan_summary),
            "feature_id": (
                plan_summary.get("feature_id")
                if isinstance(plan_summary, dict)
                else current.get("feature_id")
            ),
            "feature_name": (
                plan_summary.get("feature_name")
                if isinstance(plan_summary, dict)
                else current.get("feature_name")
            ),
            "directory": (
                plan_summary.get("directory")
                if isinstance(plan_summary, dict)
                else current.get("directory")
            ),
            "metadata_path": (
                plan_summary.get("metadata_path")
                if isinstance(plan_summary, dict)
                else current.get("metadata_path")
            ),
            "next_phase": next_phase,
            "feature_plan": plan_summary,
            "blocker": blocker,
            "updated_at": time.time(),
        }
        previous_feature_id = str(current.get("feature_id", "") or "").strip()
        new_feature_id = str(state.get("feature_id", "") or "").strip()
        same_feature = previous_feature_id and previous_feature_id == new_feature_id
        state["started_at"] = (
            float(current.get("started_at", time.time()) or time.time())
            if same_feature
            else time.time()
        )
        state["start_tokens"] = (
            int(
                current.get(
                    "start_tokens",
                    self.session_manager.token_counts.get("total", 0),
                )
                or 0
            )
            if same_feature
            else int(self.session_manager.token_counts.get("total", 0) or 0)
        )
        self.session_manager.set_feature_state(state, self.folder_context)
        self.sync_runtime_state()

    def _refresh_feature_state(
        self, metadata_path: str, *, status: str | None = None
    ):
        try:
            plan = refresh_and_persist_feature_plan(
                self.session_manager.current_session_name,
                metadata_path=metadata_path,
            )
            self._set_feature_state(
                feature_plan=summarize_feature_plan(plan),
                status=status,
            )
        except (FileNotFoundError, OSError, ValueError):
            return

    def _sync_feature_state_for_tool(
        self,
        tool_name: str,
        tool_args: dict,
        raw_result,
        structured_result,
    ):
        if tool_name in {
            "create_feature",
            "create_phases",
            "create_task",
            "get_execution_state",
            "block_task",
            "resume_task",
            "review_completed_tasks",
            "review_all_completed_tasks",
            "propose_task_diff",
            "decide_task_diff",
            "archive_task",
            "create_feature_task",
            "get_tasks",
            "get_current_task",
            "approve_feature_task",
            "update_feature_task",
            "update_task_status",
        }:
            data = {}
            if isinstance(structured_result, dict):
                data = structured_result.get("data", {}) or {}
                if isinstance(data.get("plan"), dict):
                    data = data["plan"]
            if not isinstance(data, dict) or "feature_id" not in data:
                data = self._parse_json_result(raw_result)
                if isinstance(data.get("plan"), dict):
                    data = data["plan"]
            if isinstance(data, dict) and data.get("feature_id"):
                is_plan_summary = any(
                    key in data
                    for key in (
                        "metadata_path",
                        "directory",
                        "review_status",
                        "phases",
                        "tasks",
                        "next_task",
                        "next_phase",
                    )
                )
                if is_plan_summary:
                    self._set_feature_state(feature_plan=data)
                elif tool_name in {"get_current_task", "get_tasks"}:
                    metadata_path = str(
                        (self.session_manager.get_feature_state() or {}).get(
                            "metadata_path", ""
                        )
                        or ""
                    ).strip()
                    if metadata_path:
                        self._refresh_feature_state(metadata_path)
            return

        if tool_name == "raise_blocker":
            data = {}
            if isinstance(structured_result, dict):
                data = structured_result.get("data", {}) or {}
            if not isinstance(data, dict) or not data.get("kind"):
                data = self._parse_json_result(raw_result)
            if isinstance(data, dict):
                self._set_feature_state(status="awaiting_input", blocker=data)
            return

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
                summaries.append(
                    f"file:{fr.get('display_name', fr.get('uri', 'unknown'))}"
                )

        if not summaries:
            return f"- {role}: [no serializable content]"
        return f"- {role}: " + " | ".join(summaries)

    def _prepare_runtime_history(
        self, turn_start_index: int | None = None
    ) -> list[dict]:
        if self.session_manager.summary_anchor > len(self.session_manager.history):
            self.session_manager.summary_anchor = 0
        context_limit = max(
            1024, int(self.variables.get("context_token_limit", 256000) or 256000)
        )
        trim_threshold = float(self.variables.get("context_trim_threshold", 0.85) or 0.85)
        trim_threshold = max(0.10, min(trim_threshold, 1.0))
        token_budget = max(512, int(context_limit * trim_threshold))
        start_index = len(self.session_manager.history)
        running_tokens = 0
        while start_index > self.session_manager.summary_anchor:
            next_index = start_index - 1
            next_tokens = self.session_manager._estimate_message_tokens(
                self.session_manager.history[next_index]
            )
            if running_tokens + next_tokens > token_budget and next_index < len(
                self.session_manager.history
            ) - 1:
                break
            running_tokens += next_tokens
            start_index = next_index
        recent_history = self.session_manager.history[start_index:]
        tool_window = max(0, int(self.variables.get("tool_context_window", 6)))

        if turn_start_index is None:
            return recent_history

        start_in_recent = max(
            0,
            turn_start_index - start_index,
        )
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
                "LAYER 4 — Recent tool activity (compressed for budget).\n"
                "Older tool call/result pairs from this turn were summarized.\n"
                + "\n".join(summarized_lines)
            )
            compressed_turn.insert(
                (
                    1
                    if compressed_turn and compressed_turn[0].get("role") == "user"
                    else 0
                ),
                {"role": "system", "parts": [{"type": "text", "text": summary_text}]},
            )

        return prefix + compressed_turn

    def _inject_conversation_summary(self, system_prompt: str) -> str:
        summary = str(
            getattr(self.session_manager, "conversation_summary", "") or ""
        ).strip()
        if not summary:
            return system_prompt
        return (
            f"{system_prompt}\n\n"
            "A rolling summary of older conversation history is available below. "
            "Use it for long-term continuity before re-reading or re-deriving prior work.\n"
            f"{summary}"
        )

    def _build_active_goal_context(self) -> str:
        sections = []
        loop_goal = str(self.variables.get("loop_goal", "") or "").strip()
        if loop_goal and str(self.variables.get("agent_mode", "default")).lower() == "loop":
            sections.append(f"- loop_goal: {loop_goal}")
            sections.append(
                "- loop_memory_policy: persist durable findings with save_memory and in-flight steps with save_scratchpad."
            )
        feature_state = self.session_manager.get_feature_state()
        if isinstance(feature_state, dict):
            feature_id = str(feature_state.get("feature_id", "") or "").strip()
            status = str(feature_state.get("status", "idle") or "idle")
            next_task = feature_state.get("next_task")
            if isinstance(next_task, dict):
                next_task_text = str(
                    next_task.get("title")
                    or next_task.get("task")
                    or next_task.get("name")
                    or ""
                ).strip()
            else:
                next_task_text = str(next_task or "").strip()
            phase = feature_state.get("next_phase")
            phase_title = (
                str((phase or {}).get("title", "")).strip()
                if isinstance(phase, dict)
                else ""
            )
            sections.append(f"- feature_id: {feature_id or 'n/a'}")
            sections.append(f"- status: {status}")
            if phase_title:
                sections.append(f"- active_phase: {phase_title}")
            if next_task_text:
                sections.append(f"- next_task: {next_task_text}")

        scratch = self.turn_scratchpad.render_summary(limit=8).strip()
        if scratch:
            sections.append("\nScratchpad snapshot:\n" + scratch)
        return "\n".join(sections).strip()

    def _ensure_loop_goal_persistence(self) -> None:
        if str(self.variables.get("agent_mode", "default")).lower() != "loop":
            return
        loop_goal = str(self.variables.get("loop_goal", "") or "").strip()
        if not loop_goal:
            return
        existing = self.task_memory.search("loop goal", limit=12)
        if any(loop_goal in str(entry.content or "") for entry in existing):
            return
        self.task_memory.save(
            f"Locked loop goal: {loop_goal}",
            tags=["loop", "goal", "locked"],
            source="loop_mode",
        )
        self.turn_scratchpad.save(
            f"Current loop goal: {loop_goal}",
            tags=["loop", "goal"],
            source="loop_mode",
        )

    def _build_recent_tool_context(self, max_chars: int = 8000) -> str:
        if max_chars <= 0:
            return ""
        recent = []
        consumed = 0
        for msg in reversed(self.session_manager.history):
            if msg.get("role") not in {"assistant", "tool"}:
                continue
            for part in reversed(msg.get("parts", [])):
                if part.get("type") not in {"tool_call", "tool_result"}:
                    continue
                line = self._summarize_message_parts({"role": msg.get("role"), "parts": [part]})
                if not line:
                    continue
                entry = line + "\n"
                if consumed + len(entry) > max_chars and recent:
                    return "".join(reversed(recent)).strip()
                recent.append(entry)
                consumed += len(entry)
        return "".join(reversed(recent)).strip()

    def _build_retrieved_workspace_context(self, query: str) -> str:
        if not self.folder_context or not self.folder_context.folders:
            return ""
        request = str(query or "").strip()
        if not request:
            return ""
        top_k = max(1, int(self.variables.get("retrieval_top_k", 5) or 5))
        char_budget = max(
            1, int(self.variables.get("retrieval_context_char_limit", 5000) or 5000)
        )
        self.retrieval_index.refresh_incremental(self.folder_context)
        payload = self.retrieval_index.retrieve(request, top_k=top_k, filters={})
        lines = []
        used = 0
        for item in payload.get("results", []):
            snippet = str(item.get("snippet", "") or "").strip()
            entry = (
                f"- {item.get('path')} (score={item.get('score')})\n"
                f"{snippet}\n"
            )
            if used + len(entry) > char_budget and lines:
                break
            lines.append(entry)
            used += len(entry)
        if not lines:
            return ""
        return "".join(lines).strip()

    def _inject_hierarchical_context(self, system_prompt: str) -> str:
        summary_limit = max(
            0, int(self.variables.get("conversation_summary_char_limit", 8000) or 8000)
        )
        summary = str(getattr(self.session_manager, "conversation_summary", "") or "").strip()
        if summary_limit and len(summary) > summary_limit:
            summary = summary[-summary_limit:].lstrip()

        goal_context = self._build_active_goal_context()
        tool_context = self._build_recent_tool_context(
            max_chars=max(0, int(self.variables.get("recent_tool_context_char_limit", 12000) or 12000))
        )

        layers = []
        if summary:
            layers.append(
                "LAYER 2 — Conversation summary:\n"
                f"[budget: {summary_limit} chars | eviction: keep newest]\n{summary}"
            )
        if goal_context:
            layers.append("LAYER 3 — Active task plan / current goal:\n" + goal_context)
        if tool_context:
            tool_limit = max(
                0,
                int(self.variables.get("recent_tool_context_char_limit", 12000) or 12000),
            )
            layers.append(
                "LAYER 4 — Recent tool activity (latest first):\n"
                f"[budget: {tool_limit} chars | eviction: drop oldest tool records]\n"
                + tool_context
            )
        retrieved_context = str(getattr(self, "_pending_retrieved_context", "") or "").strip()
        if retrieved_context:
            retrieval_limit = max(
                1,
                int(self.variables.get("retrieval_context_char_limit", 5000) or 5000),
            )
            if len(retrieved_context) > retrieval_limit:
                retrieved_context = retrieved_context[:retrieval_limit].rstrip()
            layers.append(
                "LAYER 4B — Retrieved workspace snippets:\n"
                f"[budget: {retrieval_limit} chars | eviction: drop lowest-ranked snippets]\n"
                + retrieved_context
            )
        layers.append(
            "LAYER 5 — Current turn:\nAlways prioritize the live user message and current turn tool results over older context."
        )
        if not layers:
            return system_prompt
        return (
            f"{system_prompt}\n\n"
            "Hierarchical runtime context (layered with independent budgets/eviction):\n"
            + "\n\n".join(layers)
        )

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
        entries = [
            line.strip() for line in str(raw_result).splitlines() if line.strip()
        ]
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

    def _parse_json_result(self, raw_result: str) -> dict:
        try:
            parsed = json.loads(str(raw_result))
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except (TypeError, json.JSONDecodeError):
            return {"preview": self._clip_preview(raw_result, 260)}

    def _unwrap_tool_envelope(self, raw_result):
        parsed = self._parse_json_result(raw_result)
        required = {"ok", "error_code", "message", "data", "artifacts", "telemetry"}
        if not isinstance(parsed, dict) or not required.issubset(parsed.keys()):
            return None, raw_result
        message = parsed.get("message", "")
        data = parsed.get("data")
        if isinstance(message, str) and message.strip():
            return parsed, message
        if isinstance(data, str):
            return parsed, data
        return parsed, raw_result

    def _build_feature_mode_prompt(self, text: str) -> str:
        base_instruction = (
            "FEATURE MODE DIRECTIVE: use the feature-task engine for this request. First call create_feature to create canonical session-managed feature metadata, then create_phases, then create_task for each planned ticket. "
            "Legacy fallback: create_feature_task is allowed only when a single-call bootstrap is explicitly requested. "
            "Do not create alternate planning documents and do not begin code implementation until the user has reviewed and approved the plan. "
            "After approval, call get_current_task/get_tasks at the start of every implementation turn, work on only the next incomplete task, and keep task state synchronized via tool calls only. "
            "Use get_execution_state to identify the next pending phase/task, use block_task when work cannot continue without user input, and use resume_task after the user provides unblock context. "
            "Use update_task_status/approve_feature_task/get_tasks/get_current_task exclusively to read or change task status. "
            "Every task must define explicit EXIT CRITERIA, and you may set update_task_status(..., status='completed') only after all exit criteria for that task are demonstrably met and verified in the current codebase/tests. "
            "As each criterion is satisfied, call update_task_status with cumulative verified_exit_criteria so progress is visible in the task UI. "
            "In review mode, use review_all_completed_tasks first, then review_completed_tasks with categorized issues (bug/risk/enhancement), propose_task_diff for proposed fixes, decide_task_diff for user approvals/denials, and archive_task once tasks become archive-ready. "
            "Harness execution model: progress one task at a time, validate, then move to the next task. Never batch multiple tasks in one step. "
            "For investigation-heavy turns, gather read-only context first, use save_scratchpad for temporary phase notes, and call flush before acting on the collected context. "
            "Memory discipline is mandatory: use save_memory for durable facts/decisions that must survive long loops; use save_scratchpad for short-lived hypotheses and in-flight notes each turn; query memory/scratchpad before re-reading large context. "
            "If you become blocked because you need a user decision or missing context, call raise_blocker with a precise summary, what you tried, and the exact input you need so the harness can pause and ask the user for help. "
            "Do not pause after progress reports. Unless blocked or waiting on explicit approval/decision, immediately continue to the next actionable implementation step in the same run without asking the user to 'continue'. "
            "Never move to the next task until the current task's exit criteria are fully satisfied and the task is marked completed via update_task_status. "
            "When all tasks are complete, perform a review pass over the tasks and code changes together. If review fails, move failing tasks back to in_progress and continue implementing. If review succeeds, call approve_feature_task with review_status completed before you report success. "
            "In every turn response, clearly identify: current task, evidence gathered, changes made, verification result, and the immediate next step.\n\n"
        )
        return base_instruction + text

    def _build_loop_mode_prompt(self, text: str) -> str:
        loop_goal = str(
            self.variables.get("loop_goal")
            or text
            or ""
        ).strip()
        if loop_goal and not str(self.variables.get("loop_goal", "")).strip():
            self.variables["loop_goal"] = loop_goal
        base_instruction = (
            "LOOP MODE DIRECTIVE: You are executing a long-horizon loop with a locked mission. "
            "Maintain a self-directed backlog, keep exactly one active task, and continuously run plan -> execute -> verify -> re-plan cycles. "
            "Persist durable decisions using save_memory and short-term plans using save_scratchpad. "
            "After each increment, provide a timeline update with: objective, actions, evidence, decision, and next step. "
            "When blocked on user input/credentials/environment constraints, call raise_blocker with explicit unblock requirements. "
            "Do not stop unless user explicitly asks to stop loop mode.\n\n"
            f"LOCKED LOOP GOAL:\n{loop_goal}\n\n"
            "INCREMENT REQUEST:\n"
        )
        return base_instruction + text

    def _feature_doc_tool_violation(self, tool_name: str, tool_args: dict) -> str | None:
        if str(self.variables.get("agent_mode", "default")).lower() != "feature":
            return None
        feature_state = self.session_manager.get_feature_state()
        if not isinstance(feature_state, dict):
            return None
        feature_dir = str(feature_state.get("directory", "") or "").strip()
        if not feature_dir:
            return None
        if tool_name not in {"read_file", "get_chunk", "write_file", "apply_diff"}:
            return None

        arg_key = "file" if tool_name == "get_chunk" else "filename"
        target = str(tool_args.get(arg_key, "") or "").strip()
        if not target:
            return None

        if os.path.isabs(target):
            candidate = os.path.abspath(target)
        elif self.folder_context.folders:
            candidate = os.path.abspath(os.path.join(self.folder_context.folders[0], target))
        else:
            candidate = os.path.abspath(target)

        feature_root = os.path.abspath(feature_dir)
        if not candidate.startswith(feature_root + os.sep):
            return None

        filename = os.path.basename(candidate)
        if filename == "feature_plan.json":
            return (
                "Feature status files are managed by the feature-task engine. "
                f"Do not use {tool_name} on '{filename}'. "
                "Use get_tasks/get_current_task/update_task_status/approve_feature_task instead."
            )
        return None

    def _build_structured_tool_result(
        self,
        tool_name: str,
        tool_args: dict,
        raw_result,
        *,
        execution_source: str = "session",
    ):
        envelope, unwrapped_raw = self._unwrap_tool_envelope(raw_result)
        raw_text = str(unwrapped_raw)
        error_code = (
            envelope.get("error_code")
            if isinstance(envelope, dict)
            else infer_tool_error_code(tool_name, raw_text)
        )
        structured = {
            "tool_name": tool_name,
            "ok": bool(envelope.get("ok")) if isinstance(envelope, dict) else error_code is None,
            "summary": self._clip_preview(raw_text, 220),
            "args": _shorten_tool_args(tool_args),
            "raw": raw_text,
            "error_code": error_code,
            "error": (
                None
                if error_code is None
                else {
                    "code": error_code,
                    "message": self._clip_preview(raw_text, 220),
                }
            ),
            "data": {},
            "modified_files": [],
            "artifacts": [],
            "telemetry": {
                "execution_source": execution_source,
                "delivery_mode": "structured",
                "raw_char_count": len(raw_text),
                "raw_line_count": len(raw_text.splitlines()),
            },
        }
        if isinstance(envelope, dict):
            structured["telemetry"]["tool_envelope"] = envelope

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
            structured["data"] = self._parse_list_dir(
                raw_text, tool_args.get("path", "")
            )
        elif tool_name == "get_workspace_details":
            structured["data"] = self._parse_workspace_details(raw_text)
        elif tool_name in {"write_file", "apply_diff"}:
            filename = tool_args.get("filename", "")
            structured["data"] = {
                "filename": filename,
                "changed_file": filename,
            }
            if filename:
                structured["modified_files"] = [filename]
        elif tool_name == "list_agent_tasks":
            structured["data"] = self._parse_list_agent_tasks(raw_text)
        elif tool_name == "run_agent_task":
            structured["data"] = {
                "task_name": tool_args.get("task_name", ""),
                "stdout_present": "STDOUT:" in raw_text,
                "stderr_present": "STDERR:" in raw_text,
                "preview": self._clip_preview(raw_text, 260),
            }
        elif tool_name in {
            "create_feature",
            "create_phases",
            "create_task",
            "get_execution_state",
            "block_task",
            "resume_task",
            "review_completed_tasks",
            "review_all_completed_tasks",
            "propose_task_diff",
            "decide_task_diff",
            "archive_task",
            "create_feature_task",
            "update_feature_task",
            "approve_feature_task",
            "get_current_task",
            "get_tasks",
            "update_task_status",
            "raise_blocker",
        }:
            structured["data"] = self._parse_json_result(raw_text)
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

    def _execute_tool_with_memory(
        self,
        tool_name: str,
        tool_args: dict,
        *,
        invocation_source: str = "session",
    ):
        feature_violation = self._feature_doc_tool_violation(tool_name, tool_args)
        if feature_violation:
            return f"Error: {feature_violation}"

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

        return execute_tool(
            tool_name,
            tool_args,
            self.folder_context,
            self.ui,
            self.variables,
            invocation_source=invocation_source,
            session=self,
        )

    def _prompt_tool_choice(
        self, prompt_text: str, choices: list[str], default: str
    ) -> str:
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

    def _provider_error_recovery_choice(self) -> str:
        if self.ui and hasattr(self.ui, "prompt_choices"):
            return self._prompt_tool_choice(
                "Provider call failed. Choose recovery strategy:",
                choices=["retry", "rollback_retry", "abort"],
                default="retry",
            )
        if self._confirm_retry():
            return "retry"
        return "abort"

    @staticmethod
    def _is_transient_provider_error(error: Exception) -> bool:
        message = str(error or "").lower()
        transient_markers = (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "temporary failure",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
            "connection reset",
            "connection aborted",
            "network",
            "econnreset",
            "service unavailable",
            "try again",
        )
        return any(marker in message for marker in transient_markers)

    def _provider_generate_with_retry(
        self,
        *,
        messages,
        system_prompt,
        thinking,
        tools,
    ):
        retries = max(0, int(self.variables.get("provider_max_retries", 2) or 2))
        base_delay = float(self.variables.get("provider_retry_base_delay", 0.4) or 0.4)
        max_delay = float(self.variables.get("provider_retry_max_delay", 3.0) or 3.0)
        attempt = 0
        while True:
            try:
                return self.provider.generate(
                    messages=messages,
                    system_prompt=system_prompt,
                    thinking=thinking,
                    tools=tools,
                )
            except Exception as exc:
                if attempt >= retries or not self._is_transient_provider_error(exc):
                    raise
                attempt += 1
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay += random.uniform(0, min(0.2, delay * 0.25))
                if self.ui:
                    self.ui.show_info(
                        f"Transient provider error detected; retrying ({attempt}/{retries}) in {delay:.2f}s."
                    )
                time.sleep(delay)

    def _request_tool_approval(
        self,
        *,
        approval_plan: ApprovalPlan,
        display_args: dict,
        count_info: str,
    ) -> tuple[str, str | None]:
        prompt_text, choices, default = build_approval_prompt(
            approval_plan,
            display_args=display_args,
            count_info=count_info,
        )

        if self.ui and hasattr(self.ui, "request_tool_approval"):
            return self.ui.request_tool_approval(
                tool_name=approval_plan.tool_name,
                tool_args=approval_plan.tool_args,
                display_args=display_args,
                count_info=count_info,
                can_approve=approval_plan.can_approve,
                modifications=[mod.to_payload() for mod in approval_plan.modifications],
                preview_error=approval_plan.preview_error,
                error_code=approval_plan.error_code,
                prompt_text=prompt_text,
                choices=choices,
                default=default,
            )

        choice = self._prompt_tool_choice(prompt_text, choices, default)
        reason = None
        if choice == "e" and self.ui and hasattr(self.ui, "prompt"):
            reason = self.ui.prompt("Provide an explanation to the model")
        return choice, reason

    def _collect_turn_response(
        self,
        start_index: int,
        *,
        status: str,
        total_in: int,
        total_out: int,
        total_cost: float,
        error: str | None = None,
    ) -> dict:
        history_delta = self.session_manager.history[start_index:]
        assistant_messages = []
        assistant_text_parts = []
        tool_calls = []
        tool_results = []

        for message in history_delta:
            role = message.get("role")
            if role == "assistant":
                assistant_messages.append(message)
            for part in message.get("parts", []):
                part_type = part.get("type")
                if role == "assistant" and part_type == "text":
                    assistant_text_parts.append(part.get("text", ""))
                elif part_type == "tool_call":
                    tool_calls.append(
                        {
                            "tool_name": part.get("tool_name"),
                            "tool_args": part.get("tool_args", {}),
                        }
                    )
                elif part_type == "tool_result":
                    tool_results.append(
                        {
                            "tool_name": part.get("tool_name"),
                            "tool_result": part.get("tool_result"),
                        }
                    )

        return {
            "ok": error is None and status not in {"error"},
            "status": status,
            "error": error,
            "session_name": self.session_manager.current_session_name,
            "assistant_text": "\n\n".join(
                [text for text in assistant_text_parts if str(text).strip()]
            ),
            "assistant_messages": assistant_messages,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "history_delta": history_delta,
            "tokens": {
                "input": total_in,
                "output": total_out,
                "total": total_in + total_out,
                "estimated_cost": total_cost,
            },
            "session_totals": dict(self.session_manager.token_counts),
        }

    def send_message(self, text):
        logger.info(f"Sending message: {text[:100]}...")
        self.sync_runtime_state()
        if self.variables.get("scratchpad_enabled", True):
            self.turn_scratchpad.max_entries = max(
                1,
                int(
                    self.variables.get(
                        "scratchpad_max_entries", self.turn_scratchpad.max_entries
                    )
                ),
            )
            self.turn_scratchpad.clear()

        parts = list(self.staged_files)
        effective_text = text
        active_mode = str(self.variables.get("agent_mode", "default")).lower()
        if text and active_mode == "feature":
            effective_text = self._build_feature_mode_prompt(text)
        elif text and active_mode == "loop":
            effective_text = self._build_loop_mode_prompt(text)
        if active_mode == "loop":
            self._ensure_loop_goal_persistence()
        if effective_text:
            parts.append({"type": "text", "text": effective_text})

        new_user_message = {"role": "user", "parts": parts}

        if text and self.ui:
            self.ui.render_message("user", text)

        workspace_context = ""
        self._pending_retrieved_context = ""

        if self.folder_context.folders:
            retrieval_query = effective_text or text
            self._pending_retrieved_context = self._build_retrieved_workspace_context(
                retrieval_query
            )
            # Let tools auto discover workspace content as needed
            if self.agentic:
                active_tools = [t for t in TOOLS if t.name not in self.disabled_tools]
                tool_desc_str = "\n".join(
                    [f"{t.name} - {t.description}" for t in active_tools]
                )

                agent_mode = str(self.variables.get("agent_mode", "default")).lower()
                default_mode_instruction = AGENTIC_MODES.get(
                    agent_mode, AGENTIC_MODES["default"]
                )
                mode_instruction = str(
                    self.variables.get(
                        f"agentic_mode_prompt_{agent_mode}",
                        default_mode_instruction,
                    )
                    or default_mode_instruction
                )
                agentic_system_base = str(
                    self.variables.get(
                        "agentic_system_base_override", AGENTIC_SYSTEM_BASE
                    )
                    or AGENTIC_SYSTEM_BASE
                )

                # Providers automatically generated tool prompts so don't need to be embedded into the system prompt
                workspace_context = f"{agentic_system_base}\n\n### CURRENT STRATEGY MODE: {agent_mode.upper()}\n{mode_instruction}"
            else:
                logger.debug(
                    f"Using agent_mode={self.variables.get('agent_mode', 'default')}"
                )

                if self.ui:
                    with self.ui.show_status(
                        "Scanning monitored folders for changes..."
                    ):
                        if self._pending_retrieved_context:
                            workspace_context = (
                                "### RETRIEVAL-FIRST WORKSPACE CONTEXT\n"
                                "Ranked snippets were selected from semantic index scoring.\n"
                                f"{self._pending_retrieved_context}"
                            )
                        else:
                            folder_initial_xml = (
                                self.folder_context.get_initial_context_xml()
                            )
                            folder_diff_xml = self.folder_context.get_context_diff_xml()
                            workspace_context = f"{folder_initial_xml}\n\n{folder_diff_xml}"

        base_system_prompt = self.system_instruction
        if active_mode == "feature":
            base_system_prompt += (
                "\n\nFEATURE MODE SYSTEM PROMPT\n"
                "You are in Feature Plan Engine mode. "
                "Use the staged feature-task engine for this request. Start with create_feature, then create_phases, then create_task for each ticket. "
                "Do not create alternate planning documents and do not begin code implementation until the user has reviewed and approved the plan. "
                "Every task must include explicit EXIT CRITERIA and tasks can be marked completed only after all exit criteria are verified. "
                "Continuously update verified_exit_criteria via update_task_status as each criterion is met so progress remains explicit. "
                "Step through one task at a time until completion; never work multiple tasks simultaneously. "
                "Use get_execution_state to choose the next actionable phase/task, use block_task if external input is required, and resume_task when user unblock context arrives. "
                "Use review_all_completed_tasks/review_completed_tasks/propose_task_diff/decide_task_diff/archive_task for review-and-archive flow after implementation completes. "
                "gather read-only context first, use save_scratchpad for temporary phase notes, call flush before acting on collected context, and call raise_blocker when blocked on user input. "
                "You must use save_memory for durable facts/decisions and reuse search_memory/list_memory before re-deriving context in long loops. "
                "You must use save_scratchpad/list_scratchpad within each turn to track in-flight plans as context grows. "
                "Do not stall on status-only updates: unless blocked or awaiting explicit approval/decision, continue implementation autonomously until all phases and tasks are completed."
            )
        elif active_mode == "loop":
            loop_goal = str(self.variables.get("loop_goal", "") or "").strip()
            base_system_prompt += (
                "\n\nLOOP MODE SYSTEM PROMPT\n"
                "You are executing a long-horizon autonomous loop. "
                "Work continuously in increments (plan -> execute -> verify -> continue) until stopped by the user. "
                "Create and maintain your own internal task list as you progress. "
                "At each increment, provide a concise timeline update: attempted action, outcome, evidence, and next step. "
                "Use save_memory for durable findings and save_scratchpad for short-lived planning."
            )
            if loop_goal:
                base_system_prompt += f"\nLocked loop goal: {loop_goal}"
        if workspace_context:
            base_system_prompt += f"\n\n{workspace_context}"
        context_limit = max(
            1024, int(self.variables.get("context_token_limit", 256000) or 256000)
        )
        trim_threshold = float(self.variables.get("context_trim_threshold", 0.85) or 0.85)
        trim_threshold = max(0.10, min(trim_threshold, 1.0))
        self.session_manager.roll_history_summary_to_token_budget(
            int(context_limit * trim_threshold),
            keep_recent=4,
        )
        base_system_prompt = self._inject_hierarchical_context(base_system_prompt)

        recent_history = self._prepare_runtime_history()
        messages = self._build_messages_from_history(recent_history, new_user_message)

        initial_history_len = len(self.session_manager.history)
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
        provider_bad_request_retried = False

        while iteration < max_iterations:
            iteration += 1
            logger.debug(f"Agentic loop iteration {iteration}/{max_iterations}")
            current_tool_name = None
            current_tool_args = None

            try:
                dynamic_system_prompt = base_system_prompt
                if self.variables.get("memory_enabled", True):
                    self.task_memory.max_entries = max(
                        1,
                        int(
                            self.variables.get(
                                "memory_max_entries", self.task_memory.max_entries
                            )
                        ),
                    )
                    memory_summary = self.task_memory.render_summary(
                        limit=int(self.variables.get("memory_summary_limit", 8))
                    )
                    if memory_summary:
                        dynamic_system_prompt += (
                            "\n\nLAYER 3 — Persisted working memory snapshot:\n"
                            f"{memory_summary}"
                        )
                if self.variables.get("scratchpad_enabled", True):
                    scratchpad_summary = self.turn_scratchpad.render_summary(limit=8)
                    if scratchpad_summary:
                        dynamic_system_prompt += (
                            "\n\nLAYER 3 — Turn scratchpad snapshot:\n"
                            f"{scratchpad_summary}"
                        )

                if self.ui and hasattr(self.ui, "build_live_status"):
                    status_msg = self.ui.build_live_status(
                        self,
                        self.provider.model_name,
                        iteration,
                        max_iterations,
                    )
                else:
                    status_msg = (
                        f"Generating ({self.provider.model_name}) it {iteration}/{max_iterations}"
                        f" | {build_live_status_line(self)}"
                    )
                if self.ui:
                    with self.ui.show_status(status_msg):
                        response = self._provider_generate_with_retry(
                            messages=messages,
                            system_prompt=dynamic_system_prompt,
                            thinking=self.thinking,
                            tools=active_tools
                            if (self.folder_context.folders and self.agentic)
                            else None,
                        )
                else:
                    response = self._provider_generate_with_retry(
                        messages=messages,
                        system_prompt=dynamic_system_prompt,
                        thinking=self.thinking,
                        tools=active_tools
                        if (self.folder_context.folders and self.agentic)
                        else None,
                    )

                logger.debug(
                    f"Provider response received. Tokens: In {response.input_tokens}, Out {response.output_tokens}"
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
                        logger.debug(f"Assistant text: {part.text[:200]}...")
                        ai_parts_archive.append({"type": "text", "text": part.text})

                    elif part.type == "image_inline" and part.inline_data:
                        display_image_in_terminal(self.session_manager.current_session_name, part.inline_data, save=True)
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
                        if self.ui and active_mode != "loop":
                            self.ui.show_info(
                                f"🔨 Running tool: {part.tool_name}({_shorten_tool_args(part.tool_args)})"
                            )
                        logger.info(
                            f"Tool call: {part.tool_name} with args {part.tool_args}"
                        )

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
                            self._prepare_runtime_history(),
                            {"role": "system", "parts": []},
                        )[:-1]
                        continue

                    if active_mode == "loop" and iteration < max_iterations:
                        logger.info(
                            "Loop mode watchdog: assistant stopped without tool calls; issuing autonomous continue nudge."
                        )
                        watchdog_msg = {
                            "role": "user",
                            "parts": [
                                {
                                    "type": "text",
                                    "text": (
                                        "LOOP WATCHDOG: Continue autonomous loop execution now. "
                                        "Re-plan the next increment, execute concrete actions with tools, "
                                        "verify outcomes, and proceed without waiting for user confirmation. "
                                        "Only pause if blocked, and if blocked call raise_blocker with exact unblock requirements."
                                    ),
                                }
                            ],
                        }
                        self.session_manager.history.append(watchdog_msg)
                        messages = self._build_messages_from_history(
                            self._prepare_runtime_history(),
                            {"role": "system", "parts": []},
                        )[:-1]
                        continue

                    if self.ui:
                        self.ui.show_info(
                            f"Final session tokens: In {total_in} | Out {total_out} | Total {total_in + total_out} | Total Est. Cost: ${total_cost:.5f}"
                        )

                    logger.info("Agentic loop finished (no tool calls).")

                    if (
                        str(self.variables.get("agent_mode", "default")).lower()
                        == "feature"
                        and self.session_manager.get_feature_state()
                    ):
                        self._set_feature_state()

                    if self.variables.get("compact_history", False):
                        if self.ui:
                            self.ui.show_info(
                                "[dim]Compacting turn history (removing tool metadata)...[/dim]"
                            )
                            self.session_manager.compact_completed_turn()
                        logger.debug("History compacted.")

                    self.session_manager.save_history(self.folder_context)
                    return self._collect_turn_response(
                        initial_history_len,
                        status="completed",
                        total_in=total_in,
                        total_out=total_out,
                        total_cost=total_cost,
                    )

                strict_mode = self.variables.get("strict_mode", False)
                tool_result_parts = []
                tool_calls = [p for p in response.parts if p.type == "tool_call"]

                approval_plans = collect_approval_plans(
                    tool_calls,
                    self.folder_context,
                    strict_mode=strict_mode,
                    yolo=self.variables.get("yolo", False),
                )

                # Show bulk diffs if multiple
                if len(approval_plans) > 1:
                    if self.ui:
                        self.ui.show_info(
                            f"\n[bold yellow]Turn contains {len(approval_plans)} modifications requiring approval.[/bold yellow]"
                        )
                    for approval_plan in approval_plans.values():
                        for modification in approval_plan.modifications:
                            if modification.can_render_diff:
                                if self.ui:
                                    self.ui.show_diff(
                                        modification.filename,
                                        modification.original_content,
                                        modification.modified_content,
                                    )

                for i, part in enumerate(tool_calls):
                    current_tool_name = part.tool_name
                    current_tool_args = part.tool_args
                    approval_plan = approval_plans.get(i)
                    needs_approval = approval_plan is not None
                    if needs_approval:
                        result = None
                        if (
                            self.variables.get("yolo", False)
                            and approval_plan.can_approve
                        ):
                            result = self._execute_tool_with_memory(
                                part.tool_name,
                                part.tool_args,
                            )
                        if approval_plan.preview_error and self.ui:
                            for modification in approval_plan.modifications:
                                if modification.preview_error:
                                    self.ui.show_error(
                                        f"Cannot show diff for {modification.filename}: {modification.preview_error}"
                                    )
                                    logger.error(
                                        f"Diff error for {modification.filename}: {modification.preview_error}"
                                    )
                                    break

                        if (
                            approval_plan.error_code == "preview_failed"
                            and approval_plan.preview_error
                        ):
                            if self.ui:
                                self.ui.show_info(
                                    f"  [yellow]Auto-retrying malformed patch for {part.tool_name}...[/yellow]"
                                )
                            result = (
                                "Error: Malformed patch detected. Please ensure your diff is correctly "
                                f"formatted. Check hunk headers and context.\n{approval_plan.preview_error}"
                            )
                            logger.warning(
                                f"Malformed patch detected for {part.tool_name}: {approval_plan.preview_error}"
                            )

                        # Show diffs if not already shown in bulk pre-calculation
                        if result is None and len(approval_plans) <= 1:
                            for modification in approval_plan.modifications:
                                if modification.can_render_diff:
                                    if self.ui:
                                        self.ui.show_diff(
                                            modification.filename,
                                            modification.original_content,
                                            modification.modified_content,
                                        )

                        # Shorten args for display
                        display_args = _shorten_tool_args(part.tool_args)

                        # Add count info to prompt if multiple
                        count_info = (
                            f" ({i + 1}/{len(tool_calls)})"
                            if len(tool_calls) > 1
                            else ""
                        )

                        if result is None:
                            choice, reason = self._request_tool_approval(
                                approval_plan=approval_plan,
                                display_args=display_args,
                                count_info=count_info,
                            )
                            if choice == "n":
                                result = "User denied this tool call."
                                logger.info(
                                    f"Tool call {part.tool_name} denied by user."
                                )
                            elif choice == "e":
                                result = f"User denied this tool call. Reason: {reason}"
                                logger.info(
                                    f"Tool call {part.tool_name} denied by user with explanation: {reason}"
                                )
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
                    logger.debug(
                        f"Tool result ({part.tool_name}): {_sanitize_for_log(raw_result)}"
                    )
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
                            raw_result = "--- Flushed Context ---\n" + "\n\n".join(
                                collated_data
                            )
                        if self.ui:
                            self.ui.show_info(
                                f"  [Flushed {len(collated_data)} items from buffer]"
                            )
                    elif should_collate:
                        # Don't collate if there was an error
                        if raw_result and not str(raw_result).startswith("Error"):
                            self.collation_buffer.add(
                                part.tool_name, part.tool_args, raw_result
                            )
                            count = len(self.collation_buffer.entries)
                            raw_result = (
                                f"Stored '{part.tool_name}' result in collation buffer. "
                                f"{count} item(s) currently pending. "
                                "Continue gathering or call 'flush' when ready to receive all context."
                            )
                        if self.ui and active_mode != "loop":
                            self.ui.show_info(f"  [Collated: {part.tool_name}]")
                        else:
                            # If it's an error, don't collate it, let the model see the error immediately
                            if self.ui:
                                self.ui.show_tool_result(
                                    self._render_tool_result(raw_result)
                                )
                    else:
                        if self.ui and active_mode != "loop":
                            self.ui.show_tool_result(
                                self._render_tool_result(raw_result)
                            )

                    if self.ui and hasattr(self.ui, "emit_tool_trace"):
                        self.ui.emit_tool_trace(
                            part.tool_name,
                            part.tool_args,
                            source_result,
                            raw_result,
                        )

                    # --- End Collation Logic ---
                    if self.variables.get("structured_tool_results", True):
                        if raw_result != source_result:
                            _, unwrapped_source = self._unwrap_tool_envelope(
                                source_result
                            )
                            source_text = str(unwrapped_source)
                            result = self._build_structured_tool_result(
                                part.tool_name,
                                part.tool_args,
                                raw_result,
                                execution_source="session",
                            )
                            result["data"] = {
                                "collated": True,
                                "pending_items": len(self.collation_buffer.entries),
                                "source_char_count": len(source_text),
                                "source_line_count": len(source_text.splitlines()),
                            }
                            result["telemetry"].update(
                                {
                                    "delivery_mode": "collated",
                                    "visible_char_count": len(str(raw_result)),
                                }
                            )
                        else:
                            result = self._build_structured_tool_result(
                                part.tool_name,
                                part.tool_args,
                                source_result,
                                execution_source="session",
                            )
                    else:
                        result = raw_result

                    self._sync_feature_state_for_tool(
                        part.tool_name,
                        part.tool_args,
                        source_result,
                        result,
                    )
                    tool_result_parts.append(
                        {
                            "type": "tool_result",
                            "tool_name": part.tool_name,
                            "tool_result": result,
                            "thought_signature": part.thought_signature,
                        }
                    )
                    current_tool_name = None
                    current_tool_args = None

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
                if self.session_manager.get_feature_state():
                    self._set_feature_state(status="interrupted")
                return self._collect_turn_response(
                    initial_history_len,
                    status="interrupted",
                    total_in=total_in,
                    total_out=total_out,
                    total_cost=total_cost,
                    error="User interrupted execution.",
                )
            except Exception as e:
                traceback_text = traceback.format_exc()
                tool_context = ""
                if current_tool_name:
                    tool_context = (
                        f" | Last tool: {current_tool_name}("
                        f"{_shorten_tool_args(current_tool_args or {})})"
                    )
                if self.ui:
                    self.ui.show_error(f"API Error during agentic loop: {e}{tool_context}")
                    self.ui.show_error(
                        "Traceback (most recent call last):\n"
                        + "\n".join(traceback_text.strip().splitlines()[-8:])
                    )
                logger.error(f"Error in agentic loop: {e}", exc_info=True)

                if (
                    not provider_bad_request_retried
                    and not current_tool_name
                    and "400" in str(e).lower()
                ):
                    provider_bad_request_retried = True
                    self.session_manager.history = self.session_manager.history[:initial_history_len]
                    self.session_manager.summary_anchor = min(
                        self.session_manager.summary_anchor,
                        len(self.session_manager.history),
                    )
                    self.session_manager.history.append(new_user_message)
                    self.session_manager.save_history(self.folder_context)
                    messages = self._build_messages_from_history(
                        self._prepare_runtime_history(),
                        new_user_message,
                    )
                    iteration -= 1
                    if self.ui:
                        self.ui.show_info(
                            "Provider returned HTTP 400. Rolled back the current turn and retrying once."
                        )
                    continue

                choice = self._provider_error_recovery_choice()
                if choice == "rollback_retry":
                    self.session_manager.history = self.session_manager.history[: turn_start_index + 1]
                    self.session_manager.summary_anchor = min(
                        self.session_manager.summary_anchor,
                        len(self.session_manager.history),
                    )
                    self.session_manager.save_history(self.folder_context)
                    messages = self._build_messages_from_history(
                        self._prepare_runtime_history(turn_start_index),
                        {"role": "system", "parts": []},
                    )[:-1]
                    iteration -= 1
                    continue
                if choice == "retry":
                    iteration -= 1  # Decrement so the next loop run tries the same step
                    continue

                self.session_manager.save_history(self.folder_context)
                if self.session_manager.get_feature_state():
                    self._set_feature_state(status="error")
                return self._collect_turn_response(
                    initial_history_len,
                    status="error",
                    total_in=total_in,
                    total_out=total_out,
                    total_cost=total_cost,
                    error=f"{e}{tool_context}",
                )

        self.session_manager.save_history(self.folder_context)
        if self.session_manager.get_feature_state():
            self._set_feature_state(status="max_iterations_reached")
        return self._collect_turn_response(
            initial_history_len,
            status="max_iterations_reached",
            total_in=total_in,
            total_out=total_out,
            total_cost=total_cost,
            error=(
                f"Reached maximum iterations ({max_iterations}) without a final "
                "assistant response."
            ),
        )
