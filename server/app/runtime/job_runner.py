import asyncio
import copy
import json
import re
import subprocess
import textwrap
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from server.app.core.config import settings
from server.app.persistence.db import SessionLocal
from server.app.persistence.models import (
    ApprovalModel,
    ApprovalState,
    JobModel,
    JobState,
    SessionModel,
)
from server.app.policies.engine import policy_engine
from server.app.providers.router import provider_router, resolve_ordered_providers
from server.app.runtime.agent_loop import LoopStep, run_agent_loop
from server.app.runtime.orchestrator import emit_event, update_job_state
from server.app.skills.registry import skill_registry

from typing import Any

from server.app.tools.registry import (
    ApplyPatchTool,
    ClearUploadedContextStoreTool,
    CustomCommandTool,
    ExtractLinksContextTool,
    FetchPdfContextTool,
    FetchUrlContextTool,
    GetUploadedContextFileTool,
    GetWorkspaceFileContextTool,
    GitTool,
    ListUploadedContextFilesTool,
    ListWorkspaceFilesTool,
    MakefileAgentTool,
    ReadFileTool,
    ScoreSourcesTool,
    SearchArxivPapersTool,
    SearchWebContextTool,
    ToolDefinition,
    WriteFileTool,
)
from server.app.workspace.discovery import WorkspaceStore

STAGE_READY_PREFIX = "STAGE_READY::"
STAGE_NEEDS_MORE_PREFIX = "STAGE_NEEDS_MORE::"
DEFAULT_MAX_STAGE_TURNS = 3

INTERNET_ENABLED_TOOLS = {
    "fetch_url_context",
    "fetch_pdf_context",
    "extract_links_context",
    "search_web_context",
    "search_arxiv_papers",
}

INTERNAL_PROMPT_MARKERS = (
    "available_tools_by_name_and_usage:",
    "available_skills_by_name_and_usage:",
    "available_tools:",
    "available_skills:",
    "stage_protocol:",
    "response_protocol:",
    "stage_success_criteria:",
    "working_memory:",
)


RESEARCH_PROMPT_BASE = (
    "Research mode is enabled. For research requests, proactively use web and paper tools to gather evidence. "
    "Prefer search_web_context/search_arxiv_papers for discovery, fetch_url_context/fetch_pdf_context for reading, "
    "and extract_links_context to follow references. "
    "When writing findings, cite claims inline with numbered references like [1] [2]. "
    "In every research response, include a clear 'Citations' section with numbered clickable URLs used. "
    "For each key claim, include a short confidence line (high/medium/low) with a reason."
)

DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent running in MU-CLI, a terminal-gui coding assistant. "
    "MU-CLI is an open source project led by Bryan Pakulski. You are expected to be precise, safe, and helpful.\n\n"
    "Your capabilities:\n\n"
    "- Receive user prompts and other context provided by the harness, such as files in the workspace.\n"
    "- Communicate with the user by streaming thinking and responses, and by making and updating plans.\n"
    "- Emit function calls to run terminal commands and apply patches. Depending on how this run is configured, "
    "you can request these calls be escalated for approval before running.\n\n"
    "How you work\n\n"
    "Personality\n"
    "- Keep responses concise, direct, and friendly.\n"
    "- Communicate efficiently and keep the user informed about ongoing actions without unnecessary detail.\n"
    "- Prioritize actionable guidance, clearly stating assumptions, environment prerequisites, and next steps.\n"
    "- Unless explicitly asked, avoid excessively verbose explanations."
)

PLANNING_PROMPT_BASE = (
    "You are operating in human-in-the-loop developer mode. "
    "Before significant actions, provide a short plan and rationale. "
    "Prefer smallest safe changes and explain what tool(s) you need. "
    "For workspace tasks: first discover with list_workspace_files, then read only specific files with "
    "get_workspace_file_context. Do not request the whole codebase unless explicitly asked. "
    "When modifying existing files, prefer apply_patch for targeted edits; use write_file for new files or "
    "full rewrites only when explicitly requested. "
    "Before and after mutating edits, use git diff (or equivalent) to verify minimal changes. "
    "For any request involving repository state, files, diffs, or edits, tool usage is required before final "
    "claims. For mutating actions, clearly state intended edits before executing. "
    "Use an execution loop: plan -> act -> verify -> reflect -> finish. "
    "For code changes, prefer diff-oriented outputs and avoid full-file rewrites unless explicitly requested. "
    "Do not mark tasks done until you run at least one direct verification command when possible (tests, lint, "
    "type-check, or targeted checks). "
    "If progress stalls, explicitly surface blockers, propose a fallback, and request approval before risky "
    "recovery actions."
)

INTERACTIVE_PROMPT_BASE = (
    "Interactive mode is enabled. Follow a strict plan -> act -> verify loop. "
    "For file/repo tasks, use tools before making final claims. "
    "Prefer minimal, reversible edits and validate outcomes with direct checks."
)

CHAT_PROMPT_BASE = (
    "Chat mode is enabled. Respond directly and conversationally without workflow narration "
    "or stage/tool protocol unless explicitly requested by the user."
)

DEBUGGING_PROMPT_BASE = (
    "Debugging mode is enabled. Prioritize reproducibility and root-cause analysis. "
    "Capture evidence first, apply focused fixes second, and validate with targeted tests."
)

YOLO_PROMPT_BASE = (
    "YOLO mode is enabled. Execute quickly but stay coherent and explicit about risks. "
    "If blocked, surface blockers immediately and provide the best safe fallback."
)

MODE_PROMPT_BASES = {
    "chat": CHAT_PROMPT_BASE,
    "interactive": INTERACTIVE_PROMPT_BASE,
    "research": RESEARCH_PROMPT_BASE,
    "debugging": DEBUGGING_PROMPT_BASE,
    "yolo": YOLO_PROMPT_BASE,
}

def _coerce_workspace_store(value: Any) -> WorkspaceStore | None:
    if value is None:
        return None
    if isinstance(value, WorkspaceStore):
        return value
    if isinstance(value, dict):
        try:
            return WorkspaceStore.from_dict(value)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, Path):
        w = WorkspaceStore(value)
        w.attach(value)
        return w
    if isinstance(value, str):
        w = WorkspaceStore(Path(value))
        w.attach(Path(value))
        return w
    return None


def _workspace_root_for_session(session: SessionModel) -> Path | None:
    store = _coerce_workspace_store(getattr(session, "workspace", None))
    if store is None:
        return None
    if store.snapshot and getattr(store.snapshot, "root", None):
        return Path(store.snapshot.root)
    if getattr(store, "storage_dir", None):
        return Path(store.storage_dir)
    return None


def _uploaded_context_root_for_session(session: SessionModel) -> Path:
    root = _workspace_root_for_session(session) or Path.cwd()
    base = (root / ".mu" / "uploaded_context").resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _uploaded_context_session_dir_name(session: SessionModel) -> str:
    return str(getattr(session, "id", "") or "default")


def _build_custom_tools(session: SessionModel) -> list[ToolDefinition]:
    context_state = session.context_state or {}
    raw = context_state.get("custom_tools")
    if not isinstance(raw, list):
        return []

    root_getter = lambda: _workspace_root_for_session(session)
    tools: list[ToolDefinition] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        command = item.get("command")
        if not name or not description or not isinstance(command, list) or not command:
            continue
        if not all(isinstance(part, str) for part in command):
            continue
        tools.append(
            CustomCommandTool(
                name=name,
                description=description,
                command=command,
                mutating=bool(item.get("mutating", True)),
                workspace_root_getter=root_getter,
            )
        )
    return tools


def _get_runtime_tool_map(session: SessionModel) -> dict[str, ToolDefinition]:
    workspace_root_getter = lambda: _workspace_root_for_session(session)
    # Uploaded files live in <workspace>/.mu/uploaded_context/<session.id>/
    uploaded_root = _uploaded_context_root_for_session(session)
    session_name_getter = lambda: _uploaded_context_session_dir_name(session)
    store = _coerce_workspace_store(getattr(session, "workspace", None))

    tools: list[ToolDefinition] = [
        ReadFileTool(workspace_root_getter),
        WriteFileTool(workspace_root_getter),
        ApplyPatchTool(workspace_root_getter),
        GitTool(workspace_root_getter),
        ListUploadedContextFilesTool(uploaded_root, session_name_getter),
        GetUploadedContextFileTool(uploaded_root, session_name_getter),
        ClearUploadedContextStoreTool(uploaded_root, session_name_getter),
        FetchUrlContextTool(),
        SearchWebContextTool(),
        ExtractLinksContextTool(),
        SearchArxivPapersTool(),
        FetchPdfContextTool(),
        ScoreSourcesTool(),
        MakefileAgentTool(workspace_root_getter),
    ]

    if store is not None:
        tools.extend(
            [
                ListWorkspaceFilesTool(store),
                GetWorkspaceFileContextTool(store),
            ]
        )

    tools.extend(_build_custom_tools(session))
    return {tool.name: tool for tool in tools}


def _mode_prompt_base(mode: str) -> str:
    return MODE_PROMPT_BASES.get((mode or "interactive").lower(), INTERACTIVE_PROMPT_BASE)


def _requires_tool_usage(session_mode: str, step_label: str) -> bool:
    mode = (session_mode or "").lower()
    step = (step_label or "").lower()
    return (mode == "research" and step in {"explore", "summarize"}) or (
        mode in {"interactive", "debugging"} and step in {"act", "verify", "reproduce", "test"}
    )
WORKSPACE_ACTION_KEYWORDS = (
    "file",
    "code",
    "implement",
    "fix",
    "edit",
    "update",
    "refactor",
    "write",
    "test",
    "run",
    "workspace",
    "repository",
)


def _citations_required(
    session_mode: str,
    enabled_tools: list[str] | None,
    all_tool_names: set[str],
) -> bool:
    active_tool_names = set(enabled_tools) if isinstance(enabled_tools, list) and enabled_tools else all_tool_names
    return session_mode == "research" or bool(active_tool_names & INTERNET_ENABLED_TOOLS)


def _looks_like_internal_prompt_echo(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if not text.startswith("goal="):
        return False
    return any(marker in text for marker in INTERNAL_PROMPT_MARKERS)


def _is_user_facing_context_message(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    role = str(item.get("role") or "").strip().lower()
    if role not in {"user", "assistant", "system"}:
        return False
    content = str(item.get("content") or "")
    if not content.strip():
        return False
    if any(marker in content for marker in INTERNAL_PROMPT_MARKERS):
        return False
    return True


def _clip_text(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _context_importance_score(message: dict, index: int, total: int) -> int:
    role = str(message.get("role") or "").strip().lower()
    content = str(message.get("content") or "")
    score = min(40, len(content) // 20)
    if role == "assistant":
        score += 12
    if role == "user":
        score += 8
    lowered = content.lower()
    if any(
        flag in lowered
        for flag in ("error", "failed", "exception", "blocker", "verify", "evidence", "test", "tool")
    ):
        score += 25
    recency_bonus = max(0, index - max(0, total - 10))
    return score + recency_bonus


def _build_weighted_context_block(messages: list[dict], max_chars: int) -> str:
    user_facing = [item for item in messages if _is_user_facing_context_message(item)]
    if not user_facing:
        return ""

    ranked_rows: list[tuple[int, str]] = []
    total = len(user_facing)
    for index, item in enumerate(user_facing):
        role = str(item.get("role") or "unknown").strip().lower()
        content = _clip_text(str(item.get("content") or ""), 260)
        if not content:
            continue
        ranked_rows.append((_context_importance_score(item, index, total), f"- {role}: {content}"))

    ranked_rows.sort(key=lambda row: row[0], reverse=True)
    selected = [row for _, row in ranked_rows[:8]]
    context_block = "\n".join(selected)
    return _clip_text(context_block, max(600, max_chars))


def _should_enforce_tool_first(goal: str, step: LoopStep, available_tools: list[str], mode: str) -> bool:
    if mode.lower() in {"chat", "research"}:
        return False
    if step.label.lower() in {"plan", "summarize", "chat"}:
        return False
    if not available_tools:
        return False
    lowered_goal = (goal or "").lower()
    return any(keyword in lowered_goal for keyword in WORKSPACE_ACTION_KEYWORDS)


def _build_stage_prompt(
    *,
    goal: str,
    mode: str,
    step: LoopStep,
    chat_mode: bool,
    stage_attempt: int,
    max_stage_turns: int,
    tool_reference_lines: list[str],
    skill_reference_lines: list[str],
    stage_feedback: str,
    citations_required: bool,
    context_block: str,
    system_prompt_override: str | None,
    rules_checklist: str | None,
) -> str:
    mode_lower = (mode or "interactive").lower()
    base_sections = [DEFAULT_SYSTEM_PROMPT, _mode_prompt_base(mode_lower)]

    if not chat_mode and mode_lower != "research":
        base_sections.append(PLANNING_PROMPT_BASE)

    if chat_mode:
        stage_protocol = (
            "chat_protocol:\n"
            "- Respond directly to the user in normal chat form.\n"
            "- Do not include stage markers.\n"
            "- Do not simulate tool calls unless explicitly asked."
        )
        stage_body = f"goal={goal}\nmode={mode}\n\n{stage_protocol}"
    else:
        criteria = "\n".join(f"- {item}" for item in step.success_criteria)
        tools_block = "\n".join(tool_reference_lines) if tool_reference_lines else "- none"
        skills_block = "\n".join(skill_reference_lines) if skill_reference_lines else "- none"
        stage_body = (
            f"goal={goal}\n"
            f"mode={mode}\n"
            f"step={step.label}\n"
            f"attempt={stage_attempt}/{max_stage_turns}\n\n"
            "stage_objective:\n"
            f"{step.objective}\n\n"
            "stage_success_criteria:\n"
            f"{criteria}\n\n"
            "response_protocol:\n"
            f"- Complete stage: {STAGE_READY_PREFIX}{step.label}::\n"
            f"- Need more work: {STAGE_NEEDS_MORE_PREFIX}{step.label}::\n"
            "- Do not omit the prefix.\n"
            "- For tools use one of:\n"
            "  1) <tool_call><tool_name>NAME</tool_name><parameters>{...}</parameters></tool_call>\n"
            "  2) TOOL_CALL::NAME::{\"key\":\"value\"}\n"
            "- Parameters must be valid JSON object.\n"
            "- If the task requires evidence or workspace changes, call at least one relevant tool before STAGE_READY.\n"
            f"- Final attempt policy: if attempt={stage_attempt}/{max_stage_turns}, wrap up with a decisive STAGE_READY or STAGE_NEEDS_MORE.\n\n"
            "available_tools:\n"
            f"{tools_block}\n\n"
            "available_skills:\n"
            f"{skills_block}"
        )

    prompt_parts = [*base_sections, stage_body]

    if stage_feedback:
        prompt_parts.append(f"stage_feedback:\n{stage_feedback}")
    if citations_required:
        prompt_parts.append(
            "citation_requirements:\n"
            "- Claims from external sources must include inline markdown citations.\n"
            "- Add a `## Citations` section listing referenced URLs."
        )
    if context_block:
        prompt_parts.append(f"working_memory:\n{context_block}")
    if isinstance(system_prompt_override, str) and system_prompt_override.strip():
        prompt_parts.append(f"system_prompt_override={system_prompt_override.strip()}")
    if isinstance(rules_checklist, str) and rules_checklist.strip():
        prompt_parts.append(f"rules_checklist={rules_checklist.strip()}")

    return "\n\n".join(prompt_parts)



def _extract_requested_tool_name(output: str) -> str | None:
    calls = _extract_tool_calls(output)
    if not calls:
        return None
    return str(calls[0].get("tool_name") or "") or None


def _extract_tool_calls(output: str) -> list[dict]:
    text = output or ""
    calls: list[dict] = []

    def _append_call(tool_name: str, params: dict | None = None) -> None:
        name = (tool_name or "").strip()
        if not name:
            return
        if any(item.get("tool_name") == name for item in calls):
            return
        calls.append({"tool_name": name, "constraints": params if isinstance(params, dict) else {}})

    xml_pattern = re.compile(
        r"<tool_call>\s*<tool_name>([^<]+)</tool_name>\s*<parameters>([\s\S]*?)</parameters>\s*</tool_call>",
        re.IGNORECASE,
    )
    for match in xml_pattern.finditer(text):
        tool_name = (match.group(1) or "").strip()
        raw_params = (match.group(2) or "").strip()
        params = {}
        if raw_params:
            try:
                parsed = json.loads(raw_params)
                if isinstance(parsed, dict):
                    params = parsed
            except json.JSONDecodeError:
                params = {"raw_parameters": raw_params}
        _append_call(tool_name, params)

    simple_pattern = re.compile(r"TOOL_CALL::([a-zA-Z0-9_-]+)::(\{[\s\S]*?\})", re.IGNORECASE)
    for match in simple_pattern.finditer(text):
        tool_name = match.group(1)
        raw_params = (match.group(2) or "{}").strip()
        params: dict = {}
        try:
            parsed = json.loads(raw_params)
            if isinstance(parsed, dict):
                params = parsed
        except json.JSONDecodeError:
            params = {"raw_parameters": raw_params}
        _append_call(tool_name, params)

    fenced_json = re.search(r"```json\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced_json:
        try:
            parsed = json.loads(fenced_json.group(1))
            if isinstance(parsed, dict):
                tool_name = str(parsed.get("tool_name") or parsed.get("name") or "").strip()
                params = parsed.get("parameters") or parsed.get("constraints") or {}
                _append_call(tool_name, params if isinstance(params, dict) else {})
        except json.JSONDecodeError:
            pass

    inline_match = re.search(r"constraints\.tool_name\s*=\s*([a-zA-Z0-9_-]+)", text)
    if inline_match:
        _append_call(inline_match.group(1), {})

    return calls

async def _run_tool(
    tool_name: str,
    session: SessionModel,
    job: JobModel,
    call_constraints: dict | None = None,
) -> dict:
    merged_args = dict(job.constraints or {})
    if isinstance(call_constraints, dict):
        merged_args.update(call_constraints)

    runtime_tools = _get_runtime_tool_map(session)
    tool = runtime_tools.get(tool_name)
    if tool is None:
        return {
            "tool_name": tool_name,
            "ok": False,
            "error": "tool not found",
        }

    context_state = session.context_state or {}
    enabled_tools = context_state.get("enabled_tools")
    if isinstance(enabled_tools, list) and enabled_tools and tool_name not in enabled_tools:
        return {
            "tool_name": tool_name,
            "ok": False,
            "error": f"tool disabled for session: {tool_name}",
        }

    try:
        result = await asyncio.to_thread(tool.run, merged_args)
    except Exception as exc:  # noqa: BLE001
        result_payload = {
            "tool_name": tool.name,
            "ok": False,
            "error": str(exc),
        }
    else:
        result_payload = {
            "tool_name": tool.name,
            "ok": bool(result.ok),
            "output": result.output,
        }
        if not result.ok:
            result_payload["error"] = result.output

    store = _coerce_workspace_store(getattr(session, "workspace", None))
    if store is not None:
        try:
            store.record_tool_run(
                tool_name=tool.name,
                args=merged_args,
                output=str(result_payload.get("output") or result_payload.get("error") or ""),
                ok=bool(result_payload.get("ok")),
            )
            session.workspace = store
        except Exception:
            pass

    return result_payload


def _fallback_tool_calls(
    *,
    session_mode: str,
    step_label: str,
    goal: str,
    stage_attempt: int,
    max_stage_turns: int,
) -> list[dict]:
    mode = (session_mode or "").lower()
    label = (step_label or "").lower()
    if mode != "research" or label != "explore":
        return []
    if stage_attempt >= max_stage_turns:
        return []

    query = (goal or "").strip()
    if not query:
        return []

    return [
        {"tool_name": "search_web_context", "constraints": {"query": query}},
        {"tool_name": "search_arxiv_papers", "constraints": {"query": query, "max_results": 5}},
    ]




def _forced_stage_wrap_output(step: LoopStep, last_signal: str, last_output: str, stage_attempts: int) -> str:
    summary = (last_output or "").strip() or "No substantive model output was returned."
    return (
        f"Stage '{step.label}' auto-wrapped after {stage_attempts} attempts. "
        f"Last signal={last_signal or 'missing'}. "
        f"Best-effort summary: {summary}"
    )

def _normalize_stage_output(output: str) -> str:
    return re.sub(r"\s+", " ", (output or "").strip()).lower()


def _should_force_stage_progress(
    *,
    signal: str,
    cleaned_output: str,
    stage_attempt: int,
    max_stage_turns: int,
    repeated_count: int,
) -> bool:
    if signal == "ready":
        return False
    if signal == "needs_more":
        return False
    if not (cleaned_output or "").strip():
        return False
    return stage_attempt >= max_stage_turns or repeated_count >= 2


def _extract_stage_signal(output: str, expected_stage: str) -> tuple[bool, str, str]:
    text = (output or "").strip()
    if not text:
        return False, "missing", ""

    marker_pattern = re.compile(
        r"STAGE_(READY|NEEDS_MORE)::([^:]+)::",
        re.IGNORECASE,
    )
    matches = list(marker_pattern.finditer(text))
    if not matches:
        return False, "missing", text

    for match in reversed(matches):
        signal = (match.group(1) or "").lower()
        stage_name = (match.group(2) or "").strip()
        if stage_name.lower() != expected_stage.lower():
            continue

        before = text[: match.start()].strip()
        after = text[match.end() :].strip()
        body = after or before or text
        return signal == "ready", signal, body

    last = matches[-1]
    signal = (last.group(1) or "").lower()
    before = text[: last.start()].strip()
    after = text[last.end() :].strip()
    body = after or before or text
    return False, signal, body


class JobRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}

    def _cancelled(self, job_id: str) -> bool:
        event = self._cancel_flags.get(job_id)
        return event.is_set() if event else False

    def _cleanup(self, job_id: str) -> None:
        self._cancel_flags.pop(job_id, None)
        self._tasks.pop(job_id, None)

    async def _handle_tool_policy(
        self,
        db,
        session: SessionModel,
        job: JobModel,
    ) -> bool:
        requested_tool = (job.constraints or {}).get("tool_name")
        if not requested_tool:
            return True

        context_state = session.context_state or {}
        enabled_tools = context_state.get("enabled_tools")
        if isinstance(enabled_tools, list) and requested_tool not in enabled_tools:
            await emit_event(
                db,
                job.session_id,
                "policy",
                {"tool_name": requested_tool, "decision": "deny", "reason": "tool disabled for session"},
                job_id=job.id,
            )
            await update_job_state(db, job.id, JobState.blocked)
            return False

        runtime_tools = _get_runtime_tool_map(session)
        tool = runtime_tools.get(str(requested_tool))
        if not tool:
            await emit_event(
                db,
                job.session_id,
                "policy",
                {"tool_name": requested_tool, "decision": "deny", "reason": "unknown tool"},
                job_id=job.id,
            )
            await update_job_state(db, job.id, JobState.failed)
            return False

        decision = policy_engine.evaluate(session.mode, tool)
        await emit_event(
            db,
            job.session_id,
            "policy",
            {
                "tool_name": tool.name,
                "decision": decision.decision,
                "reason": decision.reason,
            },
            job_id=job.id,
        )

        if decision.decision == "allow":
            return True

        if decision.decision == "deny":
            await update_job_state(db, job.id, JobState.blocked)
            return False

        if decision.decision not in {"ask", "escalate"}:
            await update_job_state(db, job.id, JobState.blocked)
            return False

        approval = ApprovalModel(
            session_id=job.session_id,
            job_id=job.id,
            tool_name=tool.name,
            reason=decision.reason,
            state=ApprovalState.pending,
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)

        await update_job_state(db, job.id, JobState.awaiting_approval)
        await emit_event(
            db,
            job.session_id,
            "approval_requested",
            {
                "approval_id": approval.id,
                "tool_name": tool.name,
                "reason": approval.reason,
                "decision": decision.decision,
            },
            job_id=job.id,
        )

        for _ in range(300):
            if self._cancelled(job.id):
                return False
            latest_approval = await db.scalar(
                select(ApprovalModel).where(ApprovalModel.id == approval.id)
            )
            if not latest_approval:
                await update_job_state(db, job.id, JobState.blocked)
                return False
            if latest_approval.state == ApprovalState.approved:
                await update_job_state(db, job.id, JobState.queued)
                return True
            if latest_approval.state == ApprovalState.denied:
                await update_job_state(db, job.id, JobState.blocked)
                return False
            await asyncio.sleep(0.1)

        await update_job_state(db, job.id, JobState.blocked)
        return False

    async def _run(self, job_id: str) -> None:
        async with SessionLocal() as db:
            job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
            if not job:
                return

            session = await db.scalar(select(SessionModel).where(SessionModel.id == job.session_id))
            if not session:
                await update_job_state(db, job_id, JobState.failed)
                return

            checkpoints = job.checkpoints or {}
            attempts = int(checkpoints.get("attempts", 0)) + 1
            checkpoints["attempts"] = attempts
            job.checkpoints = checkpoints
            await db.commit()

            if not await self._handle_tool_policy(db, session, job):
                return

            await update_job_state(db, job_id, JobState.running)
            await emit_event(
                db,
                job.session_id,
                "log",
                {"message": "job started", "attempt": attempts},
                job_id=job.id,
            )

            provider_preferences = session.provider_preferences or {}
            ordered_providers = resolve_ordered_providers(provider_preferences)
            selected_model = provider_preferences.get("model")

            def append_context_message(role: str, content: str) -> None:
                if role == "assistant" and _looks_like_internal_prompt_echo(content):
                    return
                context_state = copy.deepcopy(session.context_state or {"name": session.name, "messages": [], "summary": None, "memory_refs": [], "max_context_messages": 40})
                context_state.setdefault("messages", []).append({"role": role, "content": content, "created_at": datetime.now().isoformat(timespec="seconds")})
                max_messages = max(5, int(context_state.get("max_context_messages", 40)))
                if len(context_state["messages"]) > max_messages:
                    context_state["messages"] = context_state["messages"][-max_messages:]
                session.context_state = context_state

            async def emit_step(step: LoopStep) -> None:
                context_state = session.context_state or {}
                enabled_skills = context_state.get("enabled_skills")
                enabled_tools = context_state.get("enabled_tools")
                system_prompt_override = context_state.get("system_prompt_override")
                rules_checklist = context_state.get("rules_checklist")
                chat_mode = (session.mode or "").lower() == "chat"

                runtime_tool_map = _get_runtime_tool_map(session)
                all_tools = list(runtime_tool_map.values())
                citations_required = _citations_required(
                    session.mode,
                    enabled_tools if isinstance(enabled_tools, list) else None,
                    {tool.name for tool in all_tools},
                )

                context_messages = context_state.get("messages") if isinstance(context_state.get("messages"), list) else []
                max_context_messages = max(5, int(context_state.get("max_context_messages", 40)))
                history_window = min(20, max_context_messages)
                recent_context = context_messages[-history_window:] if history_window else []
                max_context_chars = max(1000, int(context_state.get("max_context_chars", 8000)))
                context_block = _build_weighted_context_block(recent_context, max_context_chars)

                all_skills = skill_registry.discover(session.workspace)
                tool_reference_lines = []
                for tool in all_tools:
                    approval = "requires approval" if tool.requires_approval else "no approval"
                    tool_reference_lines.append(
                        f"- {tool.name}: {tool.description}. Use when this capability is needed. "
                        f"Call by setting constraints.tool_name={tool.name}. Risk={tool.risk_level}, {approval}."
                    )
                skill_reference_lines = []
                for skill in all_skills:
                    skill_reference_lines.append(
                        f"- {skill.name}: {skill.description}. Use for specialized workflow instructions. "
                        f"Call by explicitly referencing skill '{skill.name}' in your plan/tooling rationale."
                    )

                stage_output = ""
                stage_feedback = ""
                last_provider = ""
                last_signal = "missing"
                last_cleaned_output = ""
                prior_normalized_outputs: list[str] = []
                stage_tool_calls_made = 0
                max_stage_turns = max(1, int(context_state.get("max_stage_turns", DEFAULT_MAX_STAGE_TURNS)))
                for stage_attempt in range(1, max_stage_turns + 1):
                    prompt = _build_stage_prompt(
                        goal=job.goal,
                        mode=session.mode,
                        step=step,
                        chat_mode=chat_mode,
                        stage_attempt=stage_attempt,
                        max_stage_turns=max_stage_turns,
                        tool_reference_lines=tool_reference_lines,
                        skill_reference_lines=skill_reference_lines,
                        stage_feedback=stage_feedback,
                        citations_required=citations_required,
                        context_block=context_block,
                        system_prompt_override=system_prompt_override if isinstance(system_prompt_override, str) else None,
                        rules_checklist=rules_checklist if isinstance(rules_checklist, str) else None,
                    )

                    stage_meta = {
                        "index": step.index,
                        "label": step.label,
                        "objective": step.objective,
                        "success_criteria": step.success_criteria,
                        "attempt": stage_attempt,
                        "max_attempts": max_stage_turns,
                        "status": "in_progress",
                    }
                    query_meta = {
                        "id": job.id,
                        "goal": job.goal,
                        "mode": session.mode,
                        "attempt": attempts,
                    }

                    await emit_event(
                        db,
                        job.session_id,
                        "model_request",
                        {
                            "query": query_meta,
                            "step": step.index,
                            "label": step.label,
                            "stage": stage_meta,
                            "provider_order": ordered_providers,
                            "selected_model": selected_model,
                            "prompt": prompt,
                        },
                        job_id=job.id,
                    )

                    await emit_event(
                        db,
                        job.session_id,
                        "system_prompt",
                        {
                            "query": query_meta,
                            "step": step.index,
                            "label": step.label,
                            "stage": stage_meta,
                            "goal": job.goal,
                            "mode": session.mode,
                            "provider_order": ordered_providers,
                            "selected_model": selected_model,
                            "enabled_skills": enabled_skills if isinstance(enabled_skills, list) else [],
                            "enabled_tools": enabled_tools if isinstance(enabled_tools, list) else [],
                            "available_tools": [
                                {"name": t.name, "description": t.description}
                                for t in all_tools
                            ],
                            "available_skills": [
                                {"name": s.name, "description": s.description}
                                for s in all_skills
                            ],
                            "context_messages_count": len(context_messages),
                            "context_messages_window": history_window,
                            "system_prompt_override": system_prompt_override if isinstance(system_prompt_override, str) else "",
                            "rules_checklist": rules_checklist if isinstance(rules_checklist, str) else "",
                            "prompt": prompt,
                        },
                        job_id=job.id,
                    )

                    result = await provider_router.generate_with_fallback(
                        prompt=prompt,
                        ordered_providers=ordered_providers,
                        model=selected_model,
                        max_retries=settings.provider_max_retries,
                    )

                    if chat_mode:
                        is_ready, signal, cleaned_output = True, "chat", (result.output or "").strip()
                    else:
                        is_ready, signal, cleaned_output = _extract_stage_signal(result.output, step.label)
                    last_signal = signal
                    last_cleaned_output = cleaned_output or (result.output or "")
                    await emit_event(
                        db,
                        job.session_id,
                        "model_response",
                        {
                            "query": query_meta,
                            "step": step.index,
                            "label": step.label,
                            "stage": stage_meta,
                            "stage_signal": signal,
                            "stage_ready": is_ready,
                            "provider": result.provider_name,
                            "model": selected_model,
                            "text": result.output,
                            "output_chars": len(result.output or ""),
                        },
                        job_id=job.id,
                    )

                    normalized_output = _normalize_stage_output(cleaned_output or result.output or "")
                    repeated_count = prior_normalized_outputs.count(normalized_output) if normalized_output else 0
                    if normalized_output:
                        prior_normalized_outputs.append(normalized_output)

                    requested_tool_calls = [] if chat_mode else _extract_tool_calls(result.output)
                    if not requested_tool_calls and not chat_mode:
                        requested_tool_calls = _fallback_tool_calls(
                            session_mode=session.mode,
                            step_label=step.label,
                            goal=job.goal,
                            stage_attempt=stage_attempt,
                            max_stage_turns=max_stage_turns,
                        )

                    if (
                        _should_enforce_tool_first(
                            goal=job.goal,
                            step=step,
                            available_tools=[tool.name for tool in all_tools],
                            mode=session.mode,
                        )
                        and stage_attempt < max_stage_turns
                        and not requested_tool_calls
                        and signal != "ready"
                    ):
                        stage_feedback = (
                            "Tool-first reminder: call at least one workspace tool before concluding this stage.\n"
                            "Use exact format: <tool_call><tool_name>read_file</tool_name>"
                            "<parameters>{\"path\":\"path/to/file\"}</parameters></tool_call>"
                        )
                        continue
                    if requested_tool_calls:
                        stage_tool_calls_made += len(requested_tool_calls)
                        tool_results: list[dict] = []
                        for tool_call in requested_tool_calls:
                            requested_tool_name = str(tool_call.get("tool_name") or "").strip()
                            call_constraints = tool_call.get("constraints") if isinstance(tool_call.get("constraints"), dict) else {}
                            if not requested_tool_name:
                                continue
                            await emit_event(
                                db,
                                job.session_id,
                                "tool_call",
                                {
                                    "query": query_meta,
                                    "step": step.index,
                                    "label": step.label,
                                    "tool_name": requested_tool_name,
                                    "constraints": call_constraints,
                                },
                                job_id=job.id,
                            )
                            tool_result = await _run_tool(
                                requested_tool_name,
                                session,
                                job,
                                call_constraints=call_constraints,
                            )
                            tool_results.append(tool_result)
                            await emit_event(
                                db,
                                job.session_id,
                                "tool_result",
                                {
                                    "query": query_meta,
                                    "step": step.index,
                                    "label": step.label,
                                    "tool_name": requested_tool_name,
                                    "result": tool_result,
                                },
                                job_id=job.id,
                            )

                        if tool_results:
                            if is_ready:
                                stage_output = cleaned_output
                                last_provider = result.provider_name
                                break
                            stage_feedback = (
                                "Requested tools were executed. Review results and continue this stage.\n"
                                f"tool_results={_clip_text(json.dumps(tool_results, ensure_ascii=False), 3000)}"
                            )
                            continue

                    if is_ready and _requires_tool_usage(session.mode, step.label) and stage_tool_calls_made == 0:
                        stage_feedback = (
                            "This stage requires tool-backed evidence before completion. "
                            "Call at least one relevant tool now, then continue."
                        )
                        continue

                    if is_ready:
                        stage_output = cleaned_output
                        last_provider = result.provider_name
                        break

                    if _should_force_stage_progress(
                        signal=signal,
                        cleaned_output=cleaned_output,
                        stage_attempt=stage_attempt,
                        max_stage_turns=max_stage_turns,
                        repeated_count=repeated_count,
                    ):
                        stage_output = cleaned_output
                        last_provider = result.provider_name
                        await emit_event(
                            db,
                            job.session_id,
                            "stage_forced_progress",
                            {
                                "query": query_meta,
                                "step": step.index,
                                "label": step.label,
                                "attempt": stage_attempt,
                                "max_attempts": max_stage_turns,
                                "signal": signal,
                                "reason": "repeated_or_missing_stage_signal",
                                "repeated_count": repeated_count,
                            },
                            job_id=job.id,
                        )
                        break

                    stage_feedback = (
                        f"Previous model output did not confirm completion for stage '{step.label}'. "
                        f"Expected prefix: {STAGE_READY_PREFIX}{step.label}::\n"
                        f"Stage objective: {step.objective}\n"
                        f"Stage success criteria: {'; '.join(step.success_criteria)}\n"
                        f"Received signal: {signal}\n"
                        f"Previous output:\n{result.output}"
                    )

                if not stage_output:
                    stage_output = _forced_stage_wrap_output(
                        step,
                        last_signal=last_signal,
                        last_output=last_cleaned_output,
                        stage_attempts=max_stage_turns,
                    )
                    await emit_event(
                        db,
                        job.session_id,
                        "stage_forced_progress",
                        {
                            "query": {
                                "id": job.id,
                                "goal": job.goal,
                                "mode": session.mode,
                                "attempt": attempts,
                            },
                            "step": step.index,
                            "label": step.label,
                            "attempt": max_stage_turns,
                            "max_attempts": max_stage_turns,
                            "signal": last_signal,
                            "reason": "max_attempts_exhausted_autowrap",
                            "repeated_count": 0,
                        },
                        job_id=job.id,
                    )

                job.checkpoints = {
                    "last_completed_step": step.index,
                    "mode": session.mode,
                    "attempts": attempts,
                    "provider": last_provider,
                    "model": selected_model,
                    "last_output": stage_output,
                }
                await db.commit()
                await emit_event(
                    db,
                    job.session_id,
                    "loop_step",
                    {
                        "query": {
                            "id": job.id,
                            "goal": job.goal,
                            "mode": session.mode,
                            "attempt": attempts,
                        },
                        "index": step.index,
                        "label": step.label,
                        "stage": {
                            "index": step.index,
                            "label": step.label,
                            "status": "completed",
                        },
                        "mode": session.mode,
                        "provider": last_provider,
                        "model": selected_model,
                        "output_preview": stage_output[:180],
                    },
                    job_id=job.id,
                )
                await emit_event(
                    db,
                    job.session_id,
                    "assistant_chunk",
                    {
                        "query": {
                            "id": job.id,
                            "goal": job.goal,
                            "mode": session.mode,
                            "attempt": attempts,
                        },
                        "step": step.index,
                        "label": step.label,
                        "stage": {
                            "index": step.index,
                            "label": step.label,
                            "status": "completed",
                        },
                        "text": stage_output,
                        "provider": last_provider,
                        "model": selected_model,
                    },
                    job_id=job.id,
                )

            try:
                timeout_s = max(30, int((session.context_state or {}).get("max_timeout_s", 300)))
                result = await asyncio.wait_for(
                    run_agent_loop(
                        session=session,
                        job=job,
                        emit_step=emit_step,
                        is_cancelled=lambda: self._cancelled(job_id),
                    ),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                append_context_message("assistant", "Job timed out before completion")
                await db.commit()
                await update_job_state(db, job.id, JobState.failed)
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {"message": "job failed", "error": "timeout exceeded", "attempt": attempts},
                    job_id=job.id,
                )
                return

            except Exception as exc:  # noqa: BLE001
                append_context_message("assistant", f"Job failed: {exc}")
                await db.commit()
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {"message": "job failed", "error": str(exc), "attempt": attempts},
                    job_id=job.id,
                )
                await update_job_state(db, job_id, JobState.failed)
                return

            if result["status"] == "cancelled":
                job.result_artifacts = {
                    "summary": "Job cancelled by user",
                    "mode": result["mode"],
                }
                append_context_message("assistant", "Job cancelled by user")
                await db.commit()
                await update_job_state(db, job_id, JobState.cancelled)
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {"message": "job cancelled"},
                    job_id=job.id,
                )
            else:
                job.result_artifacts = {
                    "summary": "Job completed successfully",
                    "mode": result["mode"],
                    "steps": result["steps_executed"],
                    "attempts": attempts,
                    "provider": (job.checkpoints or {}).get("provider"),
                    "model": (job.checkpoints or {}).get("model"),
                }
                append_context_message(
                    "assistant",
                    (job.checkpoints or {}).get("last_output") or "Job completed successfully",
                )
                await db.commit()
                await update_job_state(db, job_id, JobState.completed)
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {
                        "message": "job completed",
                        "attempt": attempts,
                        "provider": (job.checkpoints or {}).get("provider"),
                    },
                    job_id=job.id,
                )

    def start(self, job_id: str) -> None:
        if job_id in self._tasks and not self._tasks[job_id].done():
            return
        self._cancel_flags[job_id] = asyncio.Event()
        task = asyncio.create_task(self._run(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._cleanup(job_id))

    async def cancel(self, job_id: str) -> None:
        if job_id not in self._cancel_flags:
            self._cancel_flags[job_id] = asyncio.Event()
        self._cancel_flags[job_id].set()

    async def resume(self, job_id: str) -> None:
        self.start(job_id)


job_runner = JobRunner()
