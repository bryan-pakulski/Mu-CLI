# Session class — per-turn state container + agent-loop entry.
import os
import json
import time
import glob
import re
import random
import shutil
import traceback
import hashlib
from copy import deepcopy
from collections import defaultdict
from datetime import datetime

from mu.agent.approval import build_approval_prompt, collect_approval_plans, ApprovalPlan
from mu.agent.collation import CollationBuffer
from mu.feature.engine import refresh_and_persist_feature_plan, summarize_feature_plan
from mu.memory.stores import ScratchpadStore, TaskMemoryStore
from mu.retrieval.index import SemanticCodeIndex
from mu.retrieval.index import RETRIEVAL_INDEX as _RETRIEVAL_INDEX
from mu.workspace.folder_context import FolderContext
from providers.base import LLMProvider, Message, MessagePart, FileReference, ImageData
from mu.tools._dispatcher import execute_tool
from mu.tools._envelope import infer_tool_error_code
from mu.tools.descriptors import TOOLS, COLLATED_TOOLS
# Importing `mu.tools` triggers `@tool`-decorator registrations
# (every tool in mu/tools/<group>/handlers.py) which mirror into
# `mu.tools.descriptors.TOOLS` / `TOOL_DESCRIPTORS` /
# `mu.tools._dispatcher.TOOL_HANDLERS` so the Session loop sees them.
import mu.tools  # noqa: F401
from utils.logger import logger
from utils.helpers import get_safe_mime_type, display_image_in_terminal
from utils.runtime_metrics import build_live_status_line
from utils.config import (
    DEFAULT_SESSION_NAME,
    calculate_cost,
    AGENTIC_SYSTEM_BASE,
    AGENTIC_MODES,
    DEFAULT_VARIABLES,
    NUDGE_EMPTY_RESPONSE,
    validate_and_cast,
)


# Shared helpers live in `mu/session/helpers.py` (extracted to break the
# circular-import cycle with `mu/agent/loop_body.py` and
# `mu/session/manager.py`). Re-exported here so `mucli` and tests that
# import these names from `mu.session.session` keep working.
from mu.session.helpers import (
    _HookAbort,
    _hook_abort_envelope,
    _safe_feature_path_prefix,
    _sanitize_for_log,
    _shorten_tool_args,
    _slugify_feature_id,
    derive_feature_state_status,
)
from mu.session.history import HistoryMixin


# `SessionManager` lives in `mu/session/manager.py`. Re-exported here
# so `from mu.session.session import SessionManager` keeps working.
from mu.session.manager import SessionManager  # noqa: E402, F401


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
        self.disabled_skills: list[str] = []  # names of skills to suppress
        self.retrieval_index = _RETRIEVAL_INDEX
        self._pending_retrieved_context = ""
        self._pending_user_text = ""
        # One-shot system-prompt briefings queued by load/switch commands.
        # Drained at the top of every agent turn so the model knows it
        # just resumed an in-flight course / feature / session and can
        # re-orient without the user re-explaining state. See
        # `queue_resumption_briefing` below + the drain site in
        # `mu.agent.loop_body`.
        self._pending_resumption_briefings: list[str] = []
        self.paused_execution_text: str | None = None
        # Flips to True when `raise_blocker` fires inside the agentic
        # loop. The loop-mode watchdog reads it to know the agent
        # paused intentionally — otherwise it would keep prodding the
        # model with "continue!" and burn tokens in a wedge loop.
        self._loop_blocker_raised: bool = False
        # Flips to True when a hook returns HookResult(action="abort")
        # at any fire site. The agentic loop checks this at its
        # iteration boundary and exits cleanly with status
        # "hook_aborted". `_hook_abort_reason` carries the payload from
        # the aborting hook for the turn-response error field.
        self._hook_abort_requested: bool = False
        self._hook_abort_reason: str | None = None
        # Per-session usage tracker. Populated by the pre_tool /
        # post_tool hooks in `mu/agent/usage_tracker.py`. Surfaced via
        # `/stats`. Reset via `/stats clear`.
        import time as _time_mod

        self.tool_stats: dict = {
            "session_started_at": _time_mod.time(),
            "first_call_at": None,
            "last_call_at": None,
            "tools": {},  # name → {count, success, failed, total_ms, last_used_at, last_args}
            "skills": {},  # name → {invocations, last_used_at}
            "approvals": {"approved": 0, "denied": 0},
            "errors": {},  # error_code → count
        }
        from mu.tools.shell.background import BackgroundTaskRegistry
        self.background_tasks = BackgroundTaskRegistry()

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

        # Images route through the vision path (image_input + raw bytes), not
        # the file-ref path — provider.upload_file for OpenAI/Ollama returns a
        # local path that becomes a plain "[File: ...]" text stub, which
        # vision-capable models can't actually look at.
        if safe_mime.startswith("image/"):
            try:
                with open(file_path, "rb") as fh:
                    raw = fh.read()
            except OSError as e:
                if self.ui:
                    self.ui.show_error(f"Could not read image: {e}")
                return
            import base64 as _b64
            self.staged_files.append(
                {
                    "type": "image_input",
                    "image": {
                        "data_b64": _b64.b64encode(raw).decode("ascii"),
                        "mime_type": safe_mime,
                        "source": file_path,
                    },
                }
            )
            if self.ui:
                size_kb = max(1, len(raw) // 1024)
                self.ui.show_info(
                    f"Staged image: {os.path.basename(file_path)} ({safe_mime}, {size_kb} KB)"
                )
            return

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
        return derive_feature_state_status(feature_plan)

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
            "status": status or derive_feature_state_status(plan_summary),
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
        """Feature-state writer. Body moved to
        `mu/session/tools_glue.py:sync_feature_state_for_tool`."""
        from mu.session.tools_glue import sync_feature_state_for_tool

        return sync_feature_state_for_tool(
            self,
            tool_name,
            tool_args,
            raw_result,
            structured_result,
        )

    # Message helpers (`_build_messages_from_history`,
    # `_summarize_message_parts`, `_message_has_thought_signature`,
    # `_clip_preview`, `_prepare_runtime_history`) moved to
    # `mu/session/messages.py`. Forwarders preserve the bound-method
    # interface for the agent loop and tests.

    def _build_messages_from_history(
        self, recent_history_dicts, new_user_message_dict
    ) -> list[Message]:
        from mu.session.messages import build_messages_from_history

        return build_messages_from_history(
            recent_history_dicts, new_user_message_dict
        )

    def _message_has_thought_signature(self, msg_dict: dict) -> bool:
        from mu.session.messages import message_has_thought_signature

        return message_has_thought_signature(msg_dict)

    def _summarize_message_parts(self, msg_dict: dict) -> str:
        from mu.session.messages import summarize_message_parts

        return summarize_message_parts(msg_dict)

    # Budget helpers (`_resolve_context_limit`, `_resolve_response_reserve`,
    # `_compaction_token_budget`) moved to `mu/session/budgets.py`. These
    # forwarders preserve the bound-method interface so existing call sites
    # don't need to thread a `session` parameter around.

    def _resolve_context_limit(self) -> int:
        from mu.session.budgets import resolve_context_limit

        return resolve_context_limit(self)

    def _resolve_response_reserve(self) -> int:
        from mu.session.budgets import resolve_response_reserve

        return resolve_response_reserve(self)

    def _compaction_token_budget(self) -> int:
        from mu.session.budgets import compaction_token_budget

        return compaction_token_budget(self)

    def _prepare_runtime_history(
        self, turn_start_index: int | None = None
    ) -> list[dict]:
        """History slicing + tool-window compression. Body moved to
        `mu/session/messages.py:prepare_runtime_history`."""
        from mu.session.messages import prepare_runtime_history

        return prepare_runtime_history(self, turn_start_index)

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
        # session_goal is the mode-agnostic, top-level pinned ask. It
        # renders FIRST so it survives every compaction and reminds the
        # model what the user originally wanted across long runs.
        session_goal = str(self.variables.get("session_goal", "") or "").strip()
        if session_goal:
            sections.append(f"- session_goal (pinned): {session_goal}")
            sections.append(
                "- session_goal_policy: every meaningful action should advance "
                "this goal. If a sub-task drifts off, pause and re-anchor. "
                "Use /goal clear when the user signals the goal has shifted."
            )
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

    def _ensure_session_goal_persistence(self) -> None:
        """Mirror the live `session_goal` variable into task_memory once
        per goal value so compaction can never erase the user's original
        top-level ask. Mode-agnostic — runs every turn for every mode.

        Idempotent: searches existing memory for the goal text first and
        only writes if absent. The variable is always the source of
        truth for L3 rendering; the memory entry is a durable audit
        trace and a recovery hatch if the variable ever gets cleared
        accidentally.
        """
        session_goal = str(self.variables.get("session_goal", "") or "").strip()
        if not session_goal:
            return
        existing = self.task_memory.search("session goal", limit=12)
        if any(session_goal in str(entry.content or "") for entry in existing):
            return
        self.task_memory.save(
            f"Locked session goal: {session_goal}",
            tags=["session", "goal", "locked"],
            source="session_goal",
        )

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

    # ── Loop state management ────────────────────────────────────────

    def get_loop_state(self) -> dict:
        """Return the current loop mode state dict."""
        loop_features_raw = self.variables.get("loop_features", "")
        try:
            loop_features = json.loads(loop_features_raw) if loop_features_raw else []
        except (json.JSONDecodeError, TypeError):
            loop_features = []
        return {
            "goal": self.variables.get("loop_goal", ""),
            "active": self.variables.get("loop_active", False),
            "features": loop_features,
        }

    def start_loop(self, goal: str) -> None:
        """Activate loop mode with the given long-horizon goal."""
        self.variables["loop_goal"] = goal
        self.variables["loop_active"] = True
        self.variables["loop_features"] = json.dumps([])
        self.variables["agent_mode"] = "loop"
        self._ensure_loop_goal_persistence()
        self.session_manager.save_history(self.folder_context)

    def stop_loop(self) -> None:
        """Deactivate loop mode."""
        self.variables["loop_active"] = False
        self.session_manager.save_history(self.folder_context)

    def add_loop_feature(self, feature_id: str) -> None:
        """Record a feature created during this loop session."""
        loop_features_raw = self.variables.get("loop_features", "")
        try:
            loop_features = json.loads(loop_features_raw) if loop_features_raw else []
        except (json.JSONDecodeError, TypeError):
            loop_features = []
        loop_features.append({
            "id": feature_id,
            "timestamp": datetime.now().isoformat(),
        })
        self.variables["loop_features"] = json.dumps(loop_features)
        self.save_history()

    def get_loop_features(self) -> list:
        """Return list of feature dicts created during this loop."""
        state = self.get_loop_state()
        return state.get("features", [])

    # ── End loop state management ─────────────────────────────────────

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

    def _build_workspace_context_files(self) -> str:
        """LAYER 1 context-file aggregator. Body moved to
        `mu/session/context.py:build_workspace_context_files`."""
        from mu.session.context import build_workspace_context_files

        return build_workspace_context_files(self)

    def _build_skills_block(self) -> str:
        """LAYER 1B — render the installed skills (from `mu/skills/`,
        `~/.mu/skills/`, and `<workspace>/.mu/skills/`) into a labelled
        system-prompt block. Capped by `skills_max_chars` (default 6144).
        Mode is controlled by `skills_mode` (`"compact"` default).
        """
        try:
            from mu.skills import discover_skills, render_skills_block
        except ImportError:
            return ""
        raw = self.variables.get("skills_max_chars", 6144)
        try:
            budget = max(0, int(raw)) if raw is not None else 6144
        except (TypeError, ValueError):
            budget = 6144
        if budget == 0:
            return ""
        folders = (
            list(self.folder_context.folders)
            if self.folder_context and self.folder_context.folders
            else []
        )
        skills = discover_skills(folders)
        disabled = set(getattr(self, "disabled_skills", []) or [])
        if disabled:
            skills = [s for s in skills if s.name not in disabled]
        mode = str(self.variables.get("skills_mode", "compact") or "compact").lower()
        if mode not in {"compact", "full"}:
            mode = "compact"
        user_text = str(getattr(self, "_pending_user_text", "") or "")
        return render_skills_block(
            skills, budget=budget, user_text=user_text, mode=mode
        )

    def _inject_hierarchical_context(self, system_prompt: str) -> str:
        """Layered system-prompt assembly. Body moved to
        `mu/session/context.py:inject_hierarchical_context`."""
        from mu.session.context import inject_hierarchical_context

        return inject_hierarchical_context(self, system_prompt)

    def queue_resumption_briefing(self, briefing: str) -> None:
        """Add a one-shot resumption note to the next agent turn.

        Used by /teach load, /feature load, and session-switch paths to
        tell the agent it just resumed in-flight state: which course /
        feature is active, where the user was last, what's pending. The
        briefing flushes into the next turn's system prompt and then
        clears — it never accumulates.
        """
        text = (briefing or "").strip()
        if not text:
            return
        if not hasattr(self, "_pending_resumption_briefings"):
            self._pending_resumption_briefings = []
        self._pending_resumption_briefings.append(text)

    def _drain_resumption_briefings(self) -> str:
        """Drain queued resumption briefings into a formatted block for
        the system prompt. Returns an empty string if none are queued."""
        briefings = getattr(self, "_pending_resumption_briefings", None) or []
        if not briefings:
            return ""
        self._pending_resumption_briefings = []
        body = "\n\n".join(briefings)
        return (
            "## RESUMPTION CONTEXT\n"
            "You just resumed in-flight work. Orient against this state "
            "before responding — do NOT ask the user to re-explain.\n\n"
            f"{body}"
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
        from mu.session.messages import clip_preview

        return clip_preview(text, limit)

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
        """Structured-envelope builder. Body moved to
        `mu/session/tools_glue.py:build_structured_tool_result`."""
        from mu.session.tools_glue import build_structured_tool_result

        return build_structured_tool_result(
            self,
            tool_name,
            tool_args,
            raw_result,
            execution_source=execution_source,
        )

    def _record_hook_abort(self, point: str, abort_result) -> None:
        """Stamp the abort flag + reason. Called whenever a hook returns
        `HookResult(action="abort")` at any fire site. The agentic loop
        reads `_hook_abort_requested` at its iteration boundary and
        exits cleanly with status `"hook_aborted"`."""
        reason = abort_result.payload
        if reason is None:
            reason = "Hook requested abort"
        reason_str = str(reason)
        # First abort wins — don't let a later abort clobber the original
        # cause within the same turn.
        if not self._hook_abort_requested:
            self._hook_abort_requested = True
            self._hook_abort_reason = reason_str
            logger.info(f"Hook abort at {point}: {reason_str}")
            if self.ui is not None:
                try:
                    self.ui.show_info(f"⏹  Hook abort ({point}): {reason_str}")
                except Exception:
                    pass

    def _execute_tool_with_memory(
        self,
        tool_name: str,
        tool_args: dict,
        *,
        invocation_source: str = "session",
    ):
        """Hook-fire dispatch around the canonical executor. Body moved
        to `mu/session/tools_glue.py:execute_tool_with_memory`."""
        from mu.session.tools_glue import execute_tool_with_memory

        return execute_tool_with_memory(
            self,
            tool_name,
            tool_args,
            invocation_source=invocation_source,
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
        # No CLI UI available (server mode) — auto-retry
        return True

    def _provider_error_recovery_choice(self) -> str:
        if self.ui and hasattr(self.ui, "prompt_choices"):
            return self._prompt_tool_choice(
                "Provider call failed. Choose recovery strategy:",
                choices=["retry", "rollback_retry", "abort"],
                default="retry",
            )
        if self._confirm_retry():
            error_msg = str(getattr(self, '_last_provider_error', '') or '').lower()
            status = self._extract_http_status_code(error_msg)
            is_4xx = bool(status is not None and 400 <= status < 500)
            if is_4xx:
                return "rollback_retry"
            # In non-interactive flows, avoid infinite retry loops for errors that
            # are not classified as transient/retryable.
            if not self._is_transient_provider_error(RuntimeError(error_msg)):
                return "abort"
            return "retry"
        return "abort"

    def _announce_retryable_failure(self, tool_name: str, raw_result):
        """If `raw_result` is a structured failure envelope with `retryable=True`,
        surface its `hint` on the live UI so the human can see what the agent
        saw. Also tracks repeat retryable failures of the same (tool, fingerprint)
        and escalates to an error banner on the third strike so the user knows
        the agent is stuck.
        """
        if not self.ui:
            return
        if not bool(self.variables.get("reflective_retry_enabled", True)):
            return
        envelope = None
        if isinstance(raw_result, dict):
            envelope = raw_result
        elif isinstance(raw_result, str):
            try:
                parsed = json.loads(raw_result)
                if isinstance(parsed, dict):
                    envelope = parsed
            except (ValueError, TypeError):
                envelope = None
        if not envelope:
            return
        if envelope.get("ok") is not False:
            return
        if not envelope.get("retryable"):
            return
        error_code = str(envelope.get("error_code") or "unknown")
        hint = str(envelope.get("hint") or "").strip()
        if not hint:
            return

        # Track repeats — `_retryable_failure_counts` is a dict keyed by
        # (tool_name, error_code) -> count. Reset each turn (cleared in
        # `_collect_turn_response`).
        if not hasattr(self, "_retryable_failure_counts"):
            self._retryable_failure_counts = {}
        key = (tool_name, error_code)
        self._retryable_failure_counts[key] = self._retryable_failure_counts.get(key, 0) + 1
        count = self._retryable_failure_counts[key]

        if count >= 3:
            self.ui.show_error(
                f"🔁 {tool_name} has hit {error_code} {count}x this turn. "
                f"Hint stays the same: {hint[:160]}"
            )
        else:
            self.ui.show_info(
                f"  [retryable {error_code}] {hint[:200]}"
            )

    # Retry helpers moved to `mu/agent/retry.py`. Static-method
    # forwarders preserve the `Session._is_transient_provider_error`
    # interface used by `_HookAbort` handling and tests.

    @staticmethod
    def _is_transient_provider_error(error: Exception) -> bool:
        from mu.agent.retry import is_transient_provider_error

        return is_transient_provider_error(error)

    @staticmethod
    def _extract_http_status_code(message: str) -> int | None:
        from mu.agent.retry import extract_http_status_code

        return extract_http_status_code(message)

    @staticmethod
    def _is_retryable_http_status(status_code: int) -> bool:
        from mu.agent.retry import _RETRYABLE_HTTP_STATUS

        return status_code in _RETRYABLE_HTTP_STATUS

    # Loop-detection helpers moved to `mu/agent/loop_detection.py`.
    # Static-method forwarders preserve the `Session.<method>`
    # call sites used by the iteration loop and tests.

    @staticmethod
    def _coarse_tool_args(tool_args):
        from mu.agent.loop_detection import coarse_tool_args

        return coarse_tool_args(tool_args)

    @staticmethod
    def _tool_call_fingerprint(tool_name: str, tool_args, *, pattern_only: bool = False) -> str:
        from mu.agent.loop_detection import tool_call_fingerprint

        return tool_call_fingerprint(tool_name, tool_args, pattern_only=pattern_only)

    @staticmethod
    def _track_tool_for_loop_detection(tool_name: str, tool_args) -> bool:
        from mu.agent.loop_detection import track_tool_for_loop_detection

        return track_tool_for_loop_detection(tool_name, tool_args)

    @staticmethod
    def _is_repeated_tool_sequence(
        sequence_history: list[str], repeat_threshold: int = 3
    ) -> bool:
        from mu.agent.loop_detection import is_repeated_tool_sequence

        return is_repeated_tool_sequence(sequence_history, repeat_threshold)

    def _provider_generate_with_retry(
        self,
        *,
        messages,
        system_prompt,
        thinking,
        tools,
    ):
        """Call the provider with exponential-backoff retry on transient
        failures. Body moved to `mu/agent/retry.py:provider_generate_with_retry`."""
        from mu.agent.retry import provider_generate_with_retry

        return provider_generate_with_retry(
            self,
            messages=messages,
            system_prompt=system_prompt,
            thinking=thinking,
            tools=tools,
        )

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
        # Clear the rolling flag so a future turn starts fresh.
        self._history_rolled_this_turn = False
        # Reset per-turn retry counters so the next turn isn't penalised for
        # failures the previous turn already escalated on.
        if hasattr(self, "_retryable_failure_counts"):
            self._retryable_failure_counts.clear()
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
        """Body moved to `mu/agent/loop_body.py:run_turn`."""
        from mu.agent.loop_body import run_turn

        return run_turn(self, text)
