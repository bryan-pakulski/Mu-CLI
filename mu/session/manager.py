"""`SessionManager` — persistent per-session state.

Owns the history list, conversation summary, provider config, token
accounting, feature-mode registry, and the on-disk JSON store at
`~/.mucli/sessions/<name>/session.json`. Inherits the
token-budget-aware history-roll helpers from `HistoryMixin`.

Relocated here from `core/session.py` in Phase 5 of the refactor.
`core.session.SessionManager` re-exports this class for backward
compatibility — callers that import via the old path still work.

Tests: `tests/test_session.py` (load/save/switch/delete),
`tests/test_session_picker_state.py` (session-list interactions),
`tests/test_startup_session_picker.py` (numbered-picker fallback),
`tests/test_mu_session_history.py` (HistoryMixin round-trip via this class).
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import shutil
import time
from copy import deepcopy
from typing import Any

from core.collation import CollationBuffer
from core.memory import ScratchpadStore, TaskMemoryStore
from core.workspace import FolderContext
from utils.config import (
    DEFAULT_SESSION_NAME,
    DEFAULT_VARIABLES,
    validate_and_cast,
)
from utils.logger import logger

from .history import HistoryMixin


# `HISTORY_DIR` is read dynamically from `core.session` so the
# pre-Phase-5 `monkeypatch.setattr("core.session.HISTORY_DIR", ...)`
# pattern used by 27 tests across the suite keeps working without
# bulk rewrites. The legacy module imports HISTORY_DIR from
# `utils.config` and exposes it as a module attribute; tests rebind
# that attribute, and SessionManager reads through the legacy module
# each time it needs the path.
def _history_dir() -> str:
    """Resolve the active HISTORY_DIR. Reads `core.session.HISTORY_DIR`
    so test monkeypatches against that target still drive the
    SessionManager's persistence path."""
    from core import session as _session
    return _session.HISTORY_DIR


# These helpers live in `core/session.py` and stay there for now;
# lazy-bind on first use so this module's import is cheap and dodges
# any circular-import concern with core/session.py (which itself
# re-exports SessionManager from here).
_derive_feature_state_status = None
_slugify_feature_id_fn = None


def _bind_helpers():
    global _derive_feature_state_status, _slugify_feature_id_fn
    if _derive_feature_state_status is not None:
        return
    from core import session as _session

    _derive_feature_state_status = _session.derive_feature_state_status
    _slugify_feature_id_fn = _session._slugify_feature_id


def derive_feature_state_status(feature_plan):
    _bind_helpers()
    return _derive_feature_state_status(feature_plan)


def _slugify_feature_id(value):
    _bind_helpers()
    return _slugify_feature_id_fn(value)


class SessionManager(HistoryMixin):
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
        self.token_counts = {
            "input": 0,
            "output": 0,
            "total": 0,
            "total_cost": 0.0,
            "cached": 0,
            "reasoning": 0,
        }
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
        return os.path.join(_history_dir(), "sessions", name)

    def _load_session(self, name):
        filepath = self._get_filepath(name)
        legacy_filepath = os.path.join(_history_dir(), f"{name}.json")
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
        self.token_counts = {
            "input": 0,
            "output": 0,
            "total": 0,
            "total_cost": 0.0,
            "cached": 0,
            "reasoning": 0,
        }
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
        legacy_filepath = os.path.join(_history_dir(), f"{name}.json")
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
        if isinstance(state, dict):
            # Re-derive status from feature_plan when caller did not provide
            # an explicit status override.
            feature_plan = state.get("feature_plan")
            explicit_status = str(state.get("status", "") or "").strip()
            should_derive = (not explicit_status) or explicit_status == "completed"
            if isinstance(feature_plan, dict) and should_derive:
                derived = derive_feature_state_status(feature_plan)
                state = {**state, "status": derived}
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
        self.token_counts = {
            "input": 0,
            "output": 0,
            "total": 0,
            "total_cost": 0.0,
            "cached": 0,
            "reasoning": 0,
        }
        self.variables.clear()
        self.variables.update(DEFAULT_VARIABLES)
        self.save_history()
        if self.ui:
            self.ui.show_info(f"Started new session: '{name}'")

    def list_sessions(self):
        logger.debug("Listing sessions")
        if not os.path.exists(self._get_filepath(self.current_session_name)):
            self.save_history()

        files = glob.glob(os.path.join(_history_dir(), "sessions", "*", "session.json"))
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
        files = glob.glob(os.path.join(_history_dir(), "sessions", "*", "session.json"))
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
        self.token_counts = {
            "input": 0,
            "output": 0,
            "total": 0,
            "total_cost": 0.0,
            "cached": 0,
            "reasoning": 0,
        }
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
        self.token_counts = {
            "input": 0,
            "output": 0,
            "total": 0,
            "total_cost": 0.0,
            "cached": 0,
            "reasoning": 0,
        }
        self.feature_state = None
        self.feature_registry = {}
        self.active_feature_id = None

        feature_root = self.get_feature_metadata_root()
        if os.path.isdir(feature_root):
            for entry in glob.glob(os.path.join(feature_root, "*.json")):
                os.remove(entry)

        self.save_history()

    # History summarization & token-budget rolling moved to
    # mu/session/history.py (HistoryMixin). See top of file for the import.

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
                    elif p_type == "image_input":
                        img = part.get("image", {}) or {}
                        src = img.get("source") or img.get("mime_type", "image")
                        self.ui.show_info(f"[Attached Image: {src}]")
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


