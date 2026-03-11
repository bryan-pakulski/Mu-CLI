from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse
import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from mu_cli.agent import Agent
from mu_cli.cli import PLANNING_PROMPT_BASE
from mu_cli.core.types import Message, Role, ToolCall, UsageStats
from mu_cli.models import get_model_catalog, get_models
from mu_cli.pricing import PricingCatalog, estimate_tokens
from mu_cli.providers.echo import EchoProvider
from mu_cli.providers.gemini import GeminiProvider
from mu_cli.providers.ollama import OllamaProvider
from mu_cli.providers.openai import OpenAIProvider
from mu_cli.session import SessionState, SessionStore
from mu_cli.skills import SkillStore
from mu_cli.tools.base import Tool, ToolResult
from mu_cli.tools.filesystem import (
    ApplyPatchTool,
    ClearUploadedContextStoreTool,
    GetUploadedContextFileTool,
    GetWorkspaceFileContextTool,
    GitTool,
    ExtractLinksContextTool,
    FetchPdfContextTool,
    FetchUrlContextTool,
    SearchWebContextTool,
    SearchArxivPapersTool,
    ScoreSourcesTool,
    CustomCommandTool,
    ListUploadedContextFilesTool,
    ListWorkspaceFilesTool,
    MakefileAgentTool,
    ReadFileTool,
    WriteFileTool,
)
from mu_cli.workspace import WorkspaceStore
from mu_cli.webapp.routes_session import SessionRouteDeps, register_session_routes
from mu_cli.webapp.runtime import WebRuntime, default_usage
from mu_cli.webapp.routes_state import StateRouteDeps, register_state_routes
from mu_cli.webapp.routes_chat import ChatRouteDeps, register_chat_routes
from mu_cli.webapp.services_runtime import RuntimeMutationDeps, mutate_runtime_for_clear, mutate_runtime_for_new_session, mutate_runtime_for_settings




class RetrieveConversationSummaryTool:
    name = "retrieve_conversation_summary"
    description = "Retrieve indexed condensed conversation summaries by topic/query."
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Topic or phrase to retrieve from summary index."},
            "limit": {"type": "integer", "description": "Max summaries to return (default 3)."},
        },
        "required": ["query"],
    }
    mutating = False

    def __init__(self, runtime_getter):
        self._runtime_getter = runtime_getter

    def run(self, args: dict[str, Any]) -> ToolResult:
        runtime = self._runtime_getter()
        query = str(args.get("query", "")).strip().lower()
        limit = max(1, min(10, int(args.get("limit", 3) or 3)))
        if not query:
            return ToolResult(ok=False, output="query is required")
        matches = []
        for item in runtime.summary_index:
            hay = " ".join([str(item.get("topics", "")), str(item.get("summary", "")), str(item.get("id", ""))]).lower()
            if query in hay:
                matches.append(item)
        if not matches:
            return ToolResult(ok=True, output="No matching summary entries.")
        lines = []
        for item in matches[:limit]:
            lines.append(f"- id={item.get('id')} topics={item.get('topics')}\n  summary={item.get('summary')}")
        return ToolResult(ok=True, output="\n".join(lines))

def _default_usage() -> dict[str, float]:
    return default_usage()




def _telemetry_path() -> Path:
    path = Path('.mu_cli/telemetry.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _default_telemetry() -> dict[str, Any]:
    return {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'request_counts': {},
        'action_counts': {},
        'last_updated_at': datetime.now(timezone.utc).isoformat(),
    }


def _load_telemetry(runtime: WebRuntime) -> None:
    path = _telemetry_path()
    if not path.exists():
        runtime.telemetry = _default_telemetry()
        path.write_text(json.dumps(runtime.telemetry, indent=2), encoding='utf-8')
        return
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        payload = _default_telemetry()
    runtime.telemetry = {
        'started_at': payload.get('started_at') or datetime.now(timezone.utc).isoformat(),
        'request_counts': payload.get('request_counts') or {},
        'action_counts': payload.get('action_counts') or {},
        'last_updated_at': payload.get('last_updated_at') or datetime.now(timezone.utc).isoformat(),
    }


def _persist_telemetry(runtime: WebRuntime) -> None:
    runtime.telemetry['last_updated_at'] = datetime.now(timezone.utc).isoformat()
    _telemetry_path().write_text(json.dumps(runtime.telemetry, indent=2), encoding='utf-8')


def _record_telemetry(runtime: WebRuntime, category: str, key: str, count: int = 1) -> None:
    bucket = runtime.telemetry.setdefault(category, {})
    bucket[key] = int(bucket.get(key, 0)) + int(count)
    _persist_telemetry(runtime)


def _telemetry_snapshot(runtime: WebRuntime) -> dict[str, Any]:
    req_counts = runtime.telemetry.get('request_counts') or {}
    action_counts = runtime.telemetry.get('action_counts') or {}
    total_requests = int(sum(int(v) for v in req_counts.values()))
    tool_failures = len([line for line in (runtime.traces or []) if 'tool-run:' in str(line) and 'ok=False' in str(line)])
    approval_waits = len([line for line in (runtime.traces or []) if 'approval' in str(line).lower()])
    bg_jobs = runtime.background_jobs or {}
    bg_completed = len([j for j in bg_jobs.values() if j.get('status') == 'completed'])
    bg_failed = len([j for j in bg_jobs.values() if j.get('status') in {'failed', 'timed_out'}])

    started_at = runtime.telemetry.get('started_at')
    uptime_seconds = 0
    if started_at:
        try:
            uptime_seconds = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds()))
        except ValueError:
            uptime_seconds = 0

    return {
        'started_at': started_at,
        'last_updated_at': runtime.telemetry.get('last_updated_at'),
        'uptime_seconds': uptime_seconds,
        'request_counts': req_counts,
        'action_counts': action_counts,
        'total_requests': total_requests,
        'chat_turns': len(runtime.session_turns or []),
        'tool_failures': tool_failures,
        'approval_wait_events': approval_waits,
        'background_jobs_completed': bg_completed,
        'background_jobs_failed_or_timed_out': bg_failed,
    }


def _verification_policy_for_task(text: str) -> dict[str, Any]:
    lowered = (text or "").lower()
    checks: list[str] = []
    task_type = "general"
    if any(token in lowered for token in ("test", "bug", "fix", "failing")):
        task_type = "bugfix"
        checks.extend(["tests", "lint", "typecheck"])
    if any(token in lowered for token in ("refactor", "cleanup", "rename")):
        task_type = "refactor"
        checks.extend(["tests", "lint"])
    if any(token in lowered for token in ("security", "vuln", "dependency", "auth", "token", "secrets")):
        task_type = "security"
        checks.extend(["tests", "lint", "typecheck", "security_scan"])
    if not checks:
        checks.append("targeted_validation")
    unique = []
    seen = set()
    for item in checks:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return {"task_type": task_type, "required_checks": unique}


def _has_verification_evidence(events: list[str], required_checks: list[str]) -> tuple[bool, list[str]]:
    haystack = "\n".join(events).lower()
    evidence_map = {
        "tests": ("tool-run: name=custom", "pytest", "unittest", "cargo test", "go test", "npm test"),
        "lint": ("lint", "ruff", "eslint", "flake8", "golangci", "clippy"),
        "typecheck": ("typecheck", "mypy", "pyright", "tsc", "microsoft/pyright"),
        "security_scan": ("security", "bandit", "npm audit", "pip-audit", "trivy", "safety"),
        "targeted_validation": ("tool-run:", "report", "status:"),
    }
    missing = []
    for check in required_checks:
        probes = evidence_map.get(check, (check,))
        if not any(probe in haystack for probe in probes):
            missing.append(check)
    return (not missing), missing


def _extract_latency_ms(output: str) -> int | None:
    match = re.search(r"latency_ms=(\d+)", output or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _update_tool_reliability(runtime: WebRuntime, tool_name: str, ok: bool, latency_ms: int | None) -> None:
    artifacts = runtime.research_artifacts
    table = artifacts.setdefault("tool_reliability", {})
    row = table.setdefault(tool_name, {"runs": 0, "success": 0, "fail": 0, "avg_latency_ms": 0.0, "score": 0.5})
    row["runs"] = int(row.get("runs", 0)) + 1
    if ok:
        row["success"] = int(row.get("success", 0)) + 1
    else:
        row["fail"] = int(row.get("fail", 0)) + 1
    if latency_ms is not None:
        prev_avg = float(row.get("avg_latency_ms", 0.0))
        n = max(1, int(row["runs"]))
        row["avg_latency_ms"] = round(((prev_avg * (n - 1)) + latency_ms) / n, 2)
    success_rate = float(row.get("success", 0)) / max(1, int(row.get("runs", 0)))
    latency_penalty = min(0.25, (float(row.get("avg_latency_ms", 0.0)) / 4000.0))
    row["score"] = round(max(0.0, min(1.0, success_rate - latency_penalty)), 3)


def _tool_reliability_hint(runtime: WebRuntime) -> str:
    table = (runtime.research_artifacts or {}).get("tool_reliability", {})
    if not isinstance(table, dict) or not table:
        return "No historical tool reliability data yet. Prefer targeted tools and verify outputs."
    ranked = sorted(
        ((name, data) for name, data in table.items() if isinstance(data, dict)),
        key=lambda item: float(item[1].get("score", 0.0)),
        reverse=True,
    )
    top = ranked[:3]
    lines = []
    for name, data in top:
        lines.append(f"{name}: score={float(data.get('score', 0.0)):.2f}, runs={int(data.get('runs', 0))}")
    return "Tool reliability preference (use higher-scored tools when equivalent): " + "; ".join(lines)

def _build_provider(name: str, model: str, api_key: str | None, ollama_endpoint: str | None = None, ollama_context_window: int | None = None):
    if name == "echo":
        return EchoProvider()
    if name == "openai":
        return OpenAIProvider(model=model, api_key=api_key)
    if name == "gemini":
        return GeminiProvider(model=model, api_key=api_key)
    if name == "ollama":
        return OllamaProvider(model=model, host=ollama_endpoint, context_window=ollama_context_window)
    raise ValueError(f"Unsupported provider: {name}")


_DEBUG_LEVEL_ORDER = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def _normalize_debug_level(level: str | None) -> str:
    candidate = str(level or "info").strip().lower()
    return candidate if candidate in _DEBUG_LEVEL_ORDER else "info"


def _should_log(runtime: WebRuntime, level: str) -> bool:
    configured = _DEBUG_LEVEL_ORDER.get(_normalize_debug_level(getattr(runtime, "debug_level", "info")), 20)
    current = _DEBUG_LEVEL_ORDER.get(_normalize_debug_level(level), 20)
    return current >= configured


def _log_trace(runtime: WebRuntime, level: str, message: str) -> None:
    if not _should_log(runtime, level):
        return
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    runtime.traces.append(f"log/{_normalize_debug_level(level)}: [{stamp}] {message}")


def _provider_api_key(runtime: WebRuntime, provider_name: str | None = None) -> str | None:
    name = provider_name or runtime.provider
    if name == "openai":
        return runtime.openai_api_key
    if name == "gemini":
        return runtime.google_api_key
    return None

def _provider_ollama_endpoint(runtime: WebRuntime, provider_name: str | None = None) -> str | None:
    name = provider_name or runtime.provider
    if name == "ollama":
        return runtime.ollama_endpoint
    return None



def _is_git_repo(path: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0 and (proc.stdout or "").strip() == "true"
    except Exception:
        return False


def _git_branches(path: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(path), "branch", "--format", "%(refname:short)"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _git_current_branch(path: Path) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(path), "branch", "--show-current"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    branch = (proc.stdout or "").strip()
    return branch or None


def _discover_git_repos(root: Path, max_depth: int = 3) -> list[str]:
    repos: list[str] = []
    if not root.exists() or not root.is_dir():
        return repos
    if _is_git_repo(root):
        repos.append(str(root))
    root_depth = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        current = Path(dirpath)
        if len(current.parts) - root_depth > max_depth:
            dirnames[:] = []
            continue
        if ".git" in dirnames:
            repos.append(str(current))
            dirnames[:] = []
            continue
        dirnames[:] = [name for name in dirnames if not name.startswith('.')]
    deduped = sorted(set(repos))
    return deduped


def _planning_prompt(workspace_summary: str | None = None, git_guidance: str | None = None) -> str:
    extras: list[str] = []
    if workspace_summary:
        extras.append(f"Workspace context: {workspace_summary}")
    if git_guidance:
        extras.append(git_guidance)
    if extras:
        return f"{PLANNING_PROMPT_BASE} {' '.join(extras)}"
    return PLANNING_PROMPT_BASE


RESEARCH_PROMPT_BASE = (
    "Research mode is enabled. For research requests, proactively use web and paper tools to gather evidence. "
    "Prefer search_web_context/search_arxiv_papers for discovery, fetch_url_context/fetch_pdf_context for reading, "
    "and extract_links_context to follow references. "
    "When writing findings, cite claims inline with numbered references like [1] [2]. "
    "In every research response, include a clear 'Citations' section with numbered clickable URLs used. "
    "For each key claim, include a short confidence line (high/medium/low) with a reason."
)


def _inject_research_prompt(agent: Agent) -> None:
    already = any(
        message.role is Role.SYSTEM and message.metadata.get("kind") == "research_mode"
        for message in agent.state.messages
    )
    if already:
        return
    agent.state.messages.append(
        Message(
            role=Role.SYSTEM,
            content=RESEARCH_PROMPT_BASE,
            metadata={"kind": "research_mode"},
        )
    )


def _inject_planning(agent: Agent, workspace_summary: str | None = None, git_guidance: str | None = None) -> None:
    already = any(
        message.role is Role.SYSTEM and message.metadata.get("kind") == "agentic_planning"
        for message in agent.state.messages
    )
    if already:
        return
    agent.state.messages.append(
        Message(
            role=Role.SYSTEM,
            content=_planning_prompt(workspace_summary, git_guidance),
            metadata={"kind": "agentic_planning"},
        )
    )


def _sync_skill_prompts(runtime: WebRuntime) -> None:
    kept: list[Message] = []
    for message in runtime.agent.state.messages:
        if message.role is not Role.SYSTEM:
            kept.append(message)
            continue
        kind = message.metadata.get("kind")
        if isinstance(kind, str) and kind.startswith("skill:"):
            continue
        kept.append(message)
    runtime.agent.state.messages = kept

    if runtime.skill_store is None:
        return

    for name in runtime.enabled_skills:
        skill = runtime.skill_store.load_skill(name)
        if skill is None or not skill.content:
            continue
        runtime.agent.state.messages.append(
            Message(
                role=Role.SYSTEM,
                content=f"Skill `{skill.name}` instructions:\n\n{skill.content}",
                metadata={"kind": f"skill:{skill.name}"},
            )
        )


def _runtime_git_context(runtime: WebRuntime) -> tuple[str | None, str | None]:
    workspace = runtime.workspace_path
    if not workspace:
        return None, None
    path = Path(workspace).expanduser()
    if not path.exists() or not path.is_dir() or not _is_git_repo(path):
        return None, None
    return str(path), _git_current_branch(path)


def _git_agent_instruction(runtime: WebRuntime) -> str | None:
    repo, branch = _runtime_git_context(runtime)
    if not repo:
        return None
    branch_label = branch or "(unknown branch)"
    return (
        f"Git workflow is active for repo '{repo}' on branch '{branch_label}'. "
        "When task implementation is complete, propose raising a merge request/pull request using the git tool create_pr operation. "
        "Because mutating tool calls are approval-gated, wait for user approval before finalizing; if denied, continue iterating instead of stopping."
    )


def _new_agent(runtime: WebRuntime) -> Agent:
    provider = _build_provider(
        runtime.provider,
        runtime.model,
        _provider_api_key(runtime),
        _provider_ollama_endpoint(runtime),
        runtime.ollama_context_window,
    )

    def on_approval(tool_name: str, args: dict) -> bool:
        mode = runtime.approval_mode
        if mode == "auto":
            return True
        if mode == "deny":
            return False
        request_id = f"approval_{datetime.now(timezone.utc).timestamp()}"
        with runtime.approval_condition:
            runtime.pending_approval = {
                "id": request_id,
                "tool_name": tool_name,
                "args": args,
                "decision": None,
            }
            runtime.approval_condition.notify_all()
            runtime.approval_condition.wait_for(
                lambda: runtime.pending_approval is None
                or runtime.pending_approval.get("decision") in {"approve", "deny"},
                timeout=120,
            )

            decision = None
            if runtime.pending_approval and runtime.pending_approval.get("id") == request_id:
                decision = runtime.pending_approval.get("decision")
                runtime.pending_approval = None

        approved = decision == "approve"
        runtime.traces.append(
            f"approval: [{datetime.now(timezone.utc).isoformat(timespec='seconds')}] id={request_id} tool={tool_name} decision={'approve' if approved else 'deny'}"
        )
        return approved

    def on_model_response(message: Message, calls: list[ToolCall]) -> None:
        if not _should_log(runtime, "debug"):
            return
        runtime.traces.append(f"model: [{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {message.content}")
        for call in calls:
            runtime.traces.append(f"tool-request: [{datetime.now(timezone.utc).isoformat(timespec='seconds')}] id={call.call_id} name={call.name} args={call.args}")

    def on_tool_run(name: str, args: dict, ok: bool, output: str) -> None:
        runtime.workspace_store.record_tool_run(name, args, output, ok)
        latency_ms = _extract_latency_ms(output)
        _update_tool_reliability(runtime, name, ok, latency_ms)
        if _should_log(runtime, "debug"):
            latency_suffix = f" latency_ms={latency_ms}" if latency_ms is not None else ""
            runtime.traces.append(f"tool-run: [{datetime.now(timezone.utc).isoformat(timespec='seconds')}] name={name} ok={ok}{latency_suffix} args={args} output={output[:200]}")

    return Agent(
        provider=provider,
        tools=runtime.tools,
        on_approval=on_approval,
        on_model_response=on_model_response,
        on_tool_run=on_tool_run,
        strict_tool_usage=True,
        max_model_messages=40,
    )


def _build_custom_tools(runtime: WebRuntime, specs: list[dict]) -> tuple[list[Tool], list[str]]:
    built: list[Tool] = []
    errors: list[str] = []
    seen: set[str] = set()
    builtin_names = {tool.name for tool in runtime.base_tools}
    for idx, spec in enumerate(specs):
        if not isinstance(spec, dict):
            errors.append(f"custom_tools[{idx}] must be an object")
            continue
        name = str(spec.get("name", "")).strip()
        description = str(spec.get("description", "")).strip() or "Custom command tool"
        command = spec.get("command")
        mutating = bool(spec.get("mutating", True))
        if not name:
            errors.append(f"custom_tools[{idx}] missing name")
            continue
        if name in seen:
            errors.append(f"custom_tools[{idx}] duplicate name: {name}")
            continue
        if name in builtin_names:
            errors.append(f"custom_tools[{idx}] name conflicts with built-in tool: {name}")
            continue
        seen.add(name)
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item.strip() for item in command):
            errors.append(f"custom_tools[{idx}] command must be a non-empty string array")
            continue
        built.append(
            CustomCommandTool(
                name=name,
                description=description,
                command=command,
                mutating=mutating,
                workspace_root_getter=lambda: Path(runtime.workspace_store.snapshot.root) if runtime.workspace_store.snapshot else None,
            )
        )
    return built, errors


def _refresh_tooling(runtime: WebRuntime) -> None:
    active_base = [tool for tool in runtime.base_tools if runtime.enabled_tools.get(tool.name, True)]
    custom_tools, errors = _build_custom_tools(runtime, runtime.custom_tool_specs)
    runtime.custom_tool_errors = errors
    runtime.tools = active_base + custom_tools


def _iter_chunks(text: str, *, chunk_size: int = 48) -> list[str]:
    return [text[idx : idx + chunk_size] for idx in range(0, len(text), chunk_size)] or [""]


def _turn_report(runtime: WebRuntime, user_text: str, assistant_text: str) -> dict:
    usage = runtime.agent.last_usage or UsageStats(
        input_tokens=estimate_tokens(user_text),
        output_tokens=estimate_tokens(assistant_text),
        total_tokens=estimate_tokens(user_text) + estimate_tokens(assistant_text),
    )
    report = runtime.pricing.estimate_cost(runtime.provider, runtime.model, usage)
    return {
        "provider": report.provider,
        "model": report.model,
        "input_tokens": report.usage.input_tokens,
        "output_tokens": report.usage.output_tokens,
        "total_tokens": report.usage.total_tokens,
        "estimated_cost_usd": report.estimated_cost_usd,
    }


def _record_turn(runtime: WebRuntime, report: dict) -> None:
    runtime.session_usage["input_tokens"] += int(report["input_tokens"])
    runtime.session_usage["output_tokens"] += int(report["output_tokens"])
    runtime.session_usage["total_tokens"] += int(report["total_tokens"])
    runtime.session_usage["estimated_cost_usd"] += float(report["estimated_cost_usd"])

    runtime.session_turns.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session": runtime.session_name,
            "provider": report["provider"],
            "model": report["model"],
            "input_tokens": int(report["input_tokens"]),
            "output_tokens": int(report["output_tokens"]),
            "total_tokens": int(report["total_tokens"]),
            "estimated_cost_usd": float(report["estimated_cost_usd"]),
        }
    )


def _uploaded_context_prompt(runtime: WebRuntime) -> str | None:
    if not runtime.uploads:
        return None

    sample_names = [str(item.get("name", ""))[:48] for item in runtime.uploads[:8]]
    names = ", ".join(name for name in sample_names if name)
    extra = len(runtime.uploads) - len(sample_names)
    size_bytes = sum(int(item.get("size", 0) or 0) for item in runtime.uploads)
    if extra > 0:
        names = f"{names}, ... (+{extra} more)" if names else f"... (+{extra} more)"
    preview = f" Available: {names}." if names else ""

    return (
        f"Uploaded file context store is available with {len(runtime.uploads)} file(s), total {size_bytes} bytes.{preview}"
        " Do not request full contents unless needed. Use `list_uploaded_context_files` to inspect and"
        " `get_uploaded_context_file` only for targeted retrieval."
    )


def _condense_session_context(runtime: WebRuntime, *, window_size: int | None = None) -> dict[str, Any]:
    non_system = [m for m in runtime.agent.state.messages if m.role is not Role.SYSTEM]
    raw_window = max(2, int(window_size or runtime.condense_window or 12))
    if len(non_system) <= 4:
        return {"ok": True, "unchanged": True, "message": "not enough history to condense"}

    raw_window = min(raw_window, len(non_system) - 2)

    cutoff = max(0, len(non_system) - raw_window)
    older = non_system[:cutoff]
    recent = non_system[cutoff:]

    highlights: list[str] = []
    topics: list[str] = []
    for m in older:
        text = " ".join(str(m.content or "").split())
        if not text:
            continue
        if len(text) > 180:
            text = f"{text[:177]}..."
        prefix = "user" if m.role is Role.USER else "assistant" if m.role is Role.ASSISTANT else "tool"
        highlights.append(f"- [{prefix}] {text}")
        if len(topics) < 6:
            topics.append(text.split(" ")[0].strip(".,:;()[]{}\"'")[:24])
        if len(highlights) >= 12:
            break

    summary_prompt = (
        "You are a conversation condensation engine. Capture key facts, decisions, constraints, and unresolved items "
        "with compact bullet points suitable for later retrieval by topic."
    )
    summary_lines = [
        "Session condensed summary:",
        f"- policy: {summary_prompt}",
        f"- raw window preserved: last {raw_window} messages",
        "- key details:",
        *(highlights or ["- (no textual highlights)"]),
    ]
    summary_text = "\n".join(summary_lines)

    summary_id = f"sum_{len(runtime.summary_index)+1}"
    summary_entry = {
        "id": summary_id,
        "topics": ", ".join(dict.fromkeys([t for t in topics if t])) or "general",
        "summary": summary_text,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_count": len(older),
    }
    runtime.summary_index.append(summary_entry)

    older_set = {id(m) for m in older}
    kept_messages: list[Message] = []
    for msg in runtime.agent.state.messages:
        if msg.role is Role.SYSTEM:
            kept_messages.append(msg)
            continue
        if id(msg) in older_set:
            continue
        kept_messages.append(msg)

    summary_msg = Message(
        role=Role.TOOL_RESULT,
        name="conversation_condense",
        content=summary_text,
        metadata={
            "kind": "session_condensed_summary",
            "collapsed": True,
            "summary_id": summary_id,
            "topics": summary_entry["topics"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    kept_messages.append(summary_msg)
    runtime.agent.state.messages = kept_messages

    return {
        "ok": True,
        "condensed": True,
        "before": len(non_system),
        "after_raw_window": raw_window,
        "summary_id": summary_id,
    }


def _extract_urls(text: str) -> list[str]:
    import re

    urls = re.findall(r"https?://[\w\-./?%&=+#:~;,]+[\w/#]", text or "")
    out: list[str] = []
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _build_research_artifacts(runtime: WebRuntime) -> dict[str, Any]:
    visited: list[str] = []
    snippets: list[dict[str, str]] = []
    dedup: dict[str, dict[str, Any]] = {}
    claim_graph: dict[str, list[str]] = {}

    assistant_messages = [m for m in runtime.agent.state.messages if m.role is Role.ASSISTANT]
    tool_results = [m for m in runtime.agent.state.messages if m.role is Role.TOOL_RESULT]

    for msg in tool_results:
        urls = _extract_urls(msg.content)
        visited.extend(urls)
        snippet = msg.content.splitlines()[-1][:240] if msg.content else ""
        for url in urls:
            snippets.append({"url": url, "snippet": snippet})
            domain = urllib.parse.urlparse(url).netloc.lower()
            node = dedup.setdefault(url, {"url": url, "domain": domain, "count": 0})
            node["count"] = int(node.get("count", 0)) + 1

    for msg in assistant_messages:
        text = msg.content or ""
        if "Citations:" not in text:
            continue
        claims = [line.strip() for line in text.splitlines() if line.strip() and "[" in line and "]" in line and "http" not in line]
        citations = _extract_urls(text)
        for claim in claims[:12]:
            claim_graph[claim] = citations

    unique_visited = []
    seen = set()
    for url in visited:
        if url in seen:
            continue
        seen.add(url)
        unique_visited.append(url)

    return {
        "visited_urls": unique_visited,
        "snippets": snippets[-120:],
        "deduped_sources": list(dedup.values())[:200],
        "claim_graph": claim_graph,
    }


def _validate_claim_citations(turn_messages: list[Message]) -> tuple[bool, str]:
    import re

    assistant = next((m for m in reversed(turn_messages) if m.role is Role.ASSISTANT and (m.content or "").strip()), None)
    if assistant is None:
        return True, "no assistant content"

    text = assistant.content or ""
    refs = [int(x) for x in re.findall(r"\[(\d+)\]", text)]
    urls = _extract_urls(text)
    if refs:
        max_ref = max(refs)
        if max_ref > len(urls):
            return False, f"citation index [{max_ref}] has no URL mapping"

    tool_text = "\n".join(m.content for m in turn_messages if m.role is Role.TOOL_RESULT)
    missing = [url for url in urls if url not in tool_text]
    if missing:
        return False, f"citation URL not present in tool results: {missing[0]}"
    if refs and "confidence per claim" not in text.lower():
        return False, "missing 'Confidence per claim' section"
    return True, "ok"


def _repair_citations(runtime: WebRuntime, reason: str) -> Message:
    prompt = (
        "Repair your previous response to satisfy citation validation. "
        "Rules: every [n] must map to a URL in the Citations list, each cited URL must come from tool outputs in this turn. "
        f"Validation failure: {reason}. "
        "Rewrite answer with a 'Confidence per claim' section and concise rationale."
    )
    return runtime.agent.step(prompt)


def _run_turn_with_uploaded_context(runtime: WebRuntime, text: str, *, allow_citation_repair: bool = True) -> Message:
    uploaded_prompt = _uploaded_context_prompt(runtime)
    before = len(runtime.agent.state.messages)
    if not uploaded_prompt:
        reply = runtime.agent.step(text)
        turn_messages = runtime.agent.state.messages[before:]
        ok, reason = _validate_claim_citations(turn_messages)
        if runtime.research_mode and allow_citation_repair and not ok:
            runtime.traces.append(f"citation-validation-failed: {reason}; running repair")
            reply = _repair_citations(runtime, reason)
        runtime.research_artifacts = _build_research_artifacts(runtime)
        return reply

    runtime.agent.state.messages.append(
        Message(
            role=Role.SYSTEM,
            content=uploaded_prompt,
            metadata={"kind": "uploaded_context_ephemeral"},
        )
    )
    try:
        reply = runtime.agent.step(text)
        turn_messages = runtime.agent.state.messages[before:]
        ok, reason = _validate_claim_citations(turn_messages)
        if runtime.research_mode and allow_citation_repair and not ok:
            runtime.traces.append(f"citation-validation-failed: {reason}; running repair")
            reply = _repair_citations(runtime, reason)
        runtime.research_artifacts = _build_research_artifacts(runtime)
        return reply
    finally:
        runtime.agent.state.messages = [
            m for m in runtime.agent.state.messages if m.metadata.get("kind") != "uploaded_context_ephemeral"
        ]




def _remove_uploaded_entry(runtime: WebRuntime, name: str) -> bool:
    before = len(runtime.uploads)
    runtime.uploads = [item for item in runtime.uploads if str(item.get("name", "")) != name]
    return len(runtime.uploads) != before

def _persist(runtime: WebRuntime) -> None:
    runtime.session_store.use(runtime.session_name)
    runtime.session_store.save(
        SessionState(
            provider=runtime.provider,
            model=runtime.model,
            workspace=runtime.workspace_path,
            approval_mode=runtime.approval_mode,
            messages=runtime.agent.state.messages,
            usage_totals=runtime.session_usage,
            turns=runtime.session_turns,
            uploads=runtime.uploads,
            research_artifacts=runtime.research_artifacts,
            agentic_planning=runtime.agentic_planning,
            research_mode=runtime.research_mode,
            max_runtime_seconds=runtime.max_runtime_seconds,
            debug_level=runtime.debug_level,
            condense_enabled=runtime.condense_enabled,
            condense_window=runtime.condense_window,
            ollama_context_window=runtime.ollama_context_window,
            summary_index=runtime.summary_index,
            enabled_skills=runtime.enabled_skills,
            traces=runtime.traces,
            ollama_endpoint=runtime.ollama_endpoint,
        )
    )


def _attach_workspace_if_available(runtime: WebRuntime) -> None:
    if not runtime.workspace_path:
        return
    path = Path(runtime.workspace_path).expanduser()
    if path.exists() and path.is_dir():
        runtime.workspace_store.attach(path)


def _initialize_fresh_session_state(runtime: WebRuntime, *, reset_summary_index: bool = False) -> None:
    runtime.agent = _new_agent(runtime)
    runtime.agent.add_system_prompt(runtime.system_prompt)
    if runtime.agentic_planning:
        summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
        _inject_planning(runtime.agent, summary, _git_agent_instruction(runtime))
    if runtime.research_mode:
        _inject_research_prompt(runtime.agent)
    _sync_skill_prompts(runtime)
    runtime.session_usage = _default_usage()
    runtime.session_turns = []
    runtime.uploads = []
    runtime.research_artifacts = {}
    runtime.traces = []
    if reset_summary_index:
        runtime.summary_index = []


def _load_session(runtime: WebRuntime, session_name: str) -> bool:
    runtime.session_store.use(session_name)
    loaded = runtime.session_store.load()
    if loaded is None:
        return False

    runtime.session_name = session_name
    runtime.provider = loaded.provider
    runtime.model = loaded.model
    runtime.workspace_path = loaded.workspace
    runtime.approval_mode = loaded.approval_mode
    runtime.agent = _new_agent(runtime)
    runtime.agent.state.messages = [
        m for m in loaded.messages if m.metadata.get("kind") != "uploaded_context"
    ]
    runtime.session_usage = dict(loaded.usage_totals or _default_usage())
    runtime.session_turns = list(loaded.turns or [])
    runtime.uploads = list(loaded.uploads or [])
    runtime.research_artifacts = dict(loaded.research_artifacts or {})
    if loaded.agentic_planning is not None:
        runtime.agentic_planning = bool(loaded.agentic_planning)
    if loaded.research_mode is not None:
        runtime.research_mode = bool(loaded.research_mode)
    if loaded.max_runtime_seconds is not None:
        runtime.max_runtime_seconds = int(loaded.max_runtime_seconds)
    if loaded.debug_level is not None:
        runtime.debug_level = _normalize_debug_level(loaded.debug_level)
    if loaded.condense_enabled is not None:
        runtime.condense_enabled = bool(loaded.condense_enabled)
    if loaded.condense_window is not None:
        runtime.condense_window = int(loaded.condense_window)
    if loaded.ollama_context_window is not None:
        runtime.ollama_context_window = int(loaded.ollama_context_window)
    runtime.summary_index = list(loaded.summary_index or [])
    runtime.enabled_skills = list(loaded.enabled_skills or [])
    runtime.traces = list(loaded.traces or [])
    runtime.ollama_endpoint = loaded.ollama_endpoint
    _attach_workspace_if_available(runtime)

    if runtime.agentic_planning:
        summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
        _inject_planning(runtime.agent, summary, _git_agent_instruction(runtime))
    if runtime.research_mode:
        _inject_research_prompt(runtime.agent)
    _sync_skill_prompts(runtime)

    return True



def _clear_all_stored_data(runtime: WebRuntime) -> dict[str, int]:
    session_dir = runtime.session_store.root_dir
    uploads_dir = runtime.uploads_dir
    workspace_dir = runtime.workspace_store.storage_dir

    removed_sessions = 0
    if session_dir.exists():
        for path in session_dir.glob("*.json"):
            path.unlink(missing_ok=True)
            removed_sessions += 1

    removed_upload_files = 0
    if uploads_dir.exists():
        for path in uploads_dir.rglob("*"):
            if path.is_file():
                removed_upload_files += 1
        shutil.rmtree(uploads_dir, ignore_errors=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    removed_workspace_snapshots = 0
    if workspace_dir.exists():
        for path in workspace_dir.glob("workspace_*.json"):
            path.unlink(missing_ok=True)
            removed_workspace_snapshots += 1

    runtime.background_jobs.clear()
    runtime.pending_approval = None
    runtime.workspace_path = None
    runtime.workspace_store.snapshot = None
    runtime.session_name = "default"
    runtime.session_store.use("default")
    _initialize_fresh_session_state(runtime, reset_summary_index=True)
    _persist(runtime)
    runtime.telemetry = _default_telemetry()
    _persist_telemetry(runtime)

    return {
        "sessions": removed_sessions,
        "upload_files": removed_upload_files,
        "workspace_snapshots": removed_workspace_snapshots,
    }





def _build_session_runtime(base: WebRuntime, session_name: str) -> WebRuntime:
    session_store = SessionStore(Path(".mu_cli/sessions"), session_name)
    workspace_store = WorkspaceStore(Path(".mu_cli/workspaces"))
    runtime = WebRuntime(
        provider=base.provider,
        model=base.model,
        openai_api_key=base.openai_api_key,
        google_api_key=base.google_api_key,
        ollama_endpoint=base.ollama_endpoint,
        approval_mode=base.approval_mode,
        system_prompt=base.system_prompt,
        session_name=session_name,
        workspace_path=None,
        debug=base.debug,
        debug_level=base.debug_level,
        agentic_planning=base.agentic_planning,
        research_mode=base.research_mode,
        workspace_store=workspace_store,
        session_store=session_store,
        pricing=base.pricing,
        tools=list(base.tools),
        agent=Agent(provider=EchoProvider(), tools=list(base.tools)),
        traces=[],
        session_usage=_default_usage(),
        session_turns=[],
        uploads=[],
        uploads_dir=base.uploads_dir,
        base_tools=base.base_tools,
        enabled_tools=dict(base.enabled_tools),
        custom_tool_specs=list(base.custom_tool_specs),
        custom_tool_errors=list(base.custom_tool_errors),
        research_artifacts={},
        max_runtime_seconds=900,
        condense_enabled=False,
        condense_window=12,
        ollama_context_window=65536,
        summary_index=[],
        skill_store=base.skill_store,
        enabled_skills=list(base.enabled_skills),
        telemetry=dict(base.telemetry or {}),
    )
    _refresh_tooling(runtime)
    if not _load_session(runtime, session_name):
        _initialize_fresh_session_state(runtime)
        _persist(runtime)
    return runtime




def _mark_messages_as_metadata(messages: list[Message], *, kind: str) -> None:
    for message in messages:
        if message.role not in {Role.USER, Role.ASSISTANT}:
            continue
        message.metadata["show_in_main"] = False
        message.metadata["metadata_group"] = "automation"
        message.metadata["automation_kind"] = kind


def _is_internal_agent_loop_prompt(prompt: str) -> bool:
    normalized = " ".join((prompt or "").strip().split()).lower()
    return normalized.startswith("continue executing the approved plan.") or normalized.startswith("execute the replan.") or normalized.startswith("you appear stalled.")

def _step_internal(runtime: WebRuntime, prompt: str, kind: str) -> Message:
    before = len(runtime.agent.state.messages)
    reply = runtime.agent.step(prompt)
    _mark_messages_as_metadata(runtime.agent.state.messages[before:], kind=kind)
    return reply


def _start_background_turn(base_runtime: WebRuntime, session_name: str, text: str) -> str:
    def _normalize_progress_text(value: str | None) -> str:
        return " ".join((value or "").lower().split())

    def _is_plan_complete(value: str | None) -> bool:
        normalized = _normalize_progress_text(value)
        return "plan_complete" in normalized or "plan complete" in normalized

    def _is_stalled_response(previous: str | None, current: str | None) -> bool:
        prev = _normalize_progress_text(previous)
        cur = _normalize_progress_text(current)
        if not cur:
            return True
        if prev and cur == prev:
            return True
        return cur in {"continue", "continuing", "working on it", "still working"}

    job_id = uuid.uuid4().hex
    base_runtime.background_jobs[job_id] = {
        "id": job_id,
        "session": session_name,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "error": None,
        "report": None,
        "iterations": 0,
        "runtime_budget_seconds": int(base_runtime.max_runtime_seconds),
        "plan": None,
        "plan_approval": None,
        "last_step": None,
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0},
        "completed_flash_until": None,
        "events": [],
        "prompt": text,
        "planner_critic": None,
        "verification_policy": _verification_policy_for_task(text),
        "checkpoints": [],
        "answer_contract": None,
        "cancel_requested": False,
        "cancel_reason": None,
    }

    def runner() -> None:
        job = base_runtime.background_jobs[job_id]

        def _cancelled() -> bool:
            return bool(job.get("cancel_requested"))

        def _mark_cancelled(reason: str) -> None:
            if job.get("status") in {"killed", "completed", "failed", "timed_out"}:
                return
            job["status"] = "killed"
            job["cancel_reason"] = reason
            job["events"].append(f"status: killed ({reason})")

        try:
            isolated = _build_session_runtime(base_runtime, session_name)
            isolated.debug = True
            trace_cursor = len(isolated.traces)
            deadline = datetime.now(timezone.utc).timestamp() + max(30, int(isolated.max_runtime_seconds))
            checkpoint_store = isolated.research_artifacts.setdefault("checkpoints", {})
            restored_checkpoints = list(checkpoint_store.get(session_name, []))
            if restored_checkpoints:
                job["checkpoints"].extend(restored_checkpoints[-8:])
                job["events"].append(f"checkpoint: restored {len(restored_checkpoints[-8:])}")

            if isolated.agentic_planning:
                plan_reply = _step_internal(
                    isolated,
                    "Create an execution plan for the task below. Keep it short and actionable as numbered steps. "
                    "Start with 'PLAN:'.\n\nTask:\n" + text,
                    kind="plan_draft",
                )
                plan_text = (plan_reply.content or "").strip()
                if not plan_text or plan_text.lower().startswith("calling tool"):
                    fallback_reply = _step_internal(
                        isolated,
                        "Provide ONLY a concise numbered execution plan for the same task. "
                        "Do not call tools. Start with 'PLAN:'.",
                        kind="plan_fallback",
                    )
                    plan_text = (fallback_reply.content or "").strip()
                if not plan_text:
                    plan_text = (
                        "PLAN:\n"
                        "1) Investigate the request context.\n"
                        "2) Execute required steps safely.\n"
                        "3) Summarize outcomes and next actions."
                    )
                critic_prompt = (
                    "Review the proposed plan for completeness, risk, and verification quality. "
                    "Respond in exactly two lines: 'CRITIQUE: ...' and 'PLAN_OK: yes|no'.\n\n"
                    f"Task:\n{text}\n\nProposed plan:\n{plan_text}"
                )
                critic_reply = _step_internal(isolated, critic_prompt, kind="plan_critic")
                critic_text = (critic_reply.content or "").strip()
                plan_ok = "plan_ok: yes" in critic_text.lower()
                job["planner_critic"] = critic_text[:1200]
                job["events"].append("plan: critic_passed" if plan_ok else "plan: critic_failed")
                if not plan_ok:
                    revise_reply = _step_internal(
                        isolated,
                        "Revise the prior plan to address critique gaps. Return only a numbered plan starting with 'PLAN:'.",
                        kind="plan_revise",
                    )
                    revised_text = (revise_reply.content or "").strip()
                    if revised_text:
                        plan_text = revised_text
                        job["events"].append("plan: revised_after_critic")
                job["plan"] = plan_text
                job["events"].append("plan: drafted")
                _persist(isolated)
                if isolated.approval_mode == "auto":
                    job["plan_approval"] = "approve"
                    job["last_step"] = "Plan drafted; auto-approved"
                    job["events"].append("plan: auto_approved")
                elif isolated.approval_mode == "deny":
                    job["plan_approval"] = "deny"
                    job["events"].append("plan: denied_by_policy")
                    raise RuntimeError("Plan denied by approval policy.")
                else:
                    job["last_step"] = "Plan drafted; waiting for approval"
                    job["status"] = "awaiting_plan_approval"
                    while datetime.now(timezone.utc).timestamp() < deadline:
                        if _cancelled():
                            _mark_cancelled("user requested stop")
                            return
                        decision = job.get("plan_approval")
                        if decision in {"approve", "deny"}:
                            break
                        threading.Event().wait(0.4)
                    if _cancelled():
                        _mark_cancelled("user requested stop")
                        return
                    if job.get("plan_approval") != "approve":
                        job["events"].append("plan: denied_or_timed_out")
                        raise RuntimeError("Plan not approved before timeout or was denied.")
                    job["events"].append("plan: approved")

            total_input = 0
            total_output = 0
            total_tokens = 0
            total_cost = 0.0
            max_iterations = max(2, min(60, int(isolated.max_runtime_seconds // 25) or 24))
            no_progress_streak = 0
            previous_step = None
            replan_count = 0
            completed_by_plan = False
            policy = job.get("verification_policy") or _verification_policy_for_task(text)
            job["events"].append(
                f"verification_policy: type={policy.get('task_type')} checks={','.join(policy.get('required_checks', []))}"
            )
            reliability_hint = _tool_reliability_hint(isolated)

            approved_plan = str(job.get("plan") or "").strip()
            plan_context = (
                f"\n\nApproved plan (follow this unless tool evidence requires adaptation):\n{approved_plan}\n"
                if approved_plan
                else ""
            )
            prompt = (
                text
                + plan_context
                + "\nExecution requirements:\n"
                + f"- {reliability_hint}\n"
                + "- Decompose work into checkpoints and report checkpoint completion as you progress.\n"
                + "- Final answer must include: Confidence: <high|medium|low> and Evidence: bullets linked to tool outputs."
            )
            while datetime.now(timezone.utc).timestamp() < deadline:
                if _cancelled():
                    _mark_cancelled("user requested stop")
                    break
                if int(job["iterations"]) >= max_iterations:
                    job["events"].append(f"status: iteration_cap_reached ({max_iterations})")
                    break
                job["status"] = "running"
                before_len = len(isolated.agent.state.messages)
                reply = _run_turn_with_uploaded_context(
                    isolated,
                    prompt,
                    allow_citation_repair=(prompt == text),
                )
                turn_messages = isolated.agent.state.messages[before_len:]
                had_tool_activity = any(message.role is Role.TOOL_RESULT for message in turn_messages)
                if _is_internal_agent_loop_prompt(prompt):
                    _mark_messages_as_metadata(turn_messages, kind="agent_loop")
                report = _turn_report(isolated, prompt, reply.content)
                if len(isolated.traces) > trace_cursor:
                    job["events"].extend(isolated.traces[trace_cursor:])
                    trace_cursor = len(isolated.traces)
                    if len(job["events"]) > 120:
                        job["events"] = job["events"][-120:]
                job["last_step"] = (reply.content or "").strip()[:240]
                stalled = _is_stalled_response(previous_step, job["last_step"])
                previous_step = job["last_step"]
                _record_turn(isolated, report)
                _persist(isolated)
                total_input += int(report["input_tokens"])
                total_output += int(report["output_tokens"])
                total_tokens += int(report["total_tokens"])
                total_cost += float(report["estimated_cost_usd"])
                job["iterations"] = int(job["iterations"]) + 1
                job["usage"] = {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "total_tokens": total_tokens,
                    "estimated_cost_usd": total_cost,
                }
                checkpoint = {
                    "iteration": int(job["iterations"]),
                    "status": job.get("status"),
                    "summary": (job.get("last_step") or "")[:240],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                job["checkpoints"].append(checkpoint)
                if len(job["checkpoints"]) > 30:
                    job["checkpoints"] = job["checkpoints"][-30:]

                if _is_plan_complete(reply.content):
                    completed_by_plan = True
                    job["events"].append("status: plan_complete_detected")
                    break
                if not isolated.agentic_planning:
                    break
                if not had_tool_activity:
                    no_progress_streak = no_progress_streak + 1 if stalled else 0
                    if no_progress_streak >= 2:
                        job["events"].append("status: stalled_no_tool_progress")
                        if replan_count < 2:
                            replan_count += 1
                            replan_reply = _step_internal(
                                isolated,
                                "Generate REPLAN with 3-6 concise steps to recover from stall. "
                                "Start with 'REPLAN:'. Include one immediate next tool call recommendation.",
                                kind="replan",
                            )
                            replanned = (replan_reply.content or "").strip()
                            if replanned:
                                job["plan"] = replanned
                                job["events"].append(f"plan: replan_triggered #{replan_count}")
                                prompt = (
                                    "Execute the REPLAN. Complete the next concrete tool action now. "
                                    "When done, return 'PLAN_COMPLETE' with Confidence and Evidence sections."
                                )
                                continue
                        prompt = (
                            "You appear stalled. Provide either: "
                            "(1) one concrete next tool call with arguments, or "
                            "(2) a final response starting with 'PLAN_COMPLETE' that summarizes completed work and blockers."
                        )
                        continue
                    # Prevent repetitive continue loops when the model is already giving a final synthesis.
                    break
                no_progress_streak = 0
                prompt = (
                    "Continue executing the approved plan. Use tools as needed. "
                    "When all tasks are complete, begin your response with 'PLAN_COMPLETE'."
                )

            if job.get("status") == "killed":
                pass
            elif completed_by_plan or _is_plan_complete(job.get("last_step") or ""):
                job["status"] = "completed"
            elif datetime.now(timezone.utc).timestamp() >= deadline:
                job["status"] = "timed_out"

            job["report"] = {
                "provider": isolated.provider,
                "model": isolated.model,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_tokens,
                "estimated_cost_usd": total_cost,
            }
            if job["status"] not in {"timed_out", "killed"}:
                job["status"] = "completed"
                job["completed_flash_until"] = (
                    datetime.now(timezone.utc).timestamp() + 45
                )
                required_checks = list((policy or {}).get("required_checks", []))
                verified, missing = _has_verification_evidence(job.get("events", []), required_checks)
                answer_text = str(job.get("last_step") or "")
                has_confidence = "confidence:" in answer_text.lower()
                has_evidence = "evidence:" in answer_text.lower()
                confidence = "medium" if verified else "low"
                contract = {
                    "confidence": confidence,
                    "has_confidence_section": has_confidence,
                    "has_evidence_section": has_evidence,
                    "verified": verified,
                    "missing_checks": missing,
                }
                job["answer_contract"] = contract
                if verified:
                    job["events"].append("verification: passed")
                else:
                    job["events"].append("verification: gaps=" + ",".join(missing))
                if _is_plan_complete(job.get("last_step")):
                    job["events"].append("status: completed")
                else:
                    job["events"].append("status: completed_without_explicit_plan_complete")
        except Exception as exc:
            job["error"] = str(exc)
            if job.get("status") not in {"timed_out", "killed"}:
                job["status"] = "failed"
            job["events"].append(f"status: failed ({exc})")
        finally:
            try:
                if "isolated" in locals():
                    checkpoint_store = isolated.research_artifacts.setdefault("checkpoints", {})
                    checkpoint_store[session_name] = list(job.get("checkpoints", []))[-20:]
                    _persist(isolated)
            except Exception:
                pass
            job["finished_at"] = datetime.now(timezone.utc).isoformat()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return job_id


def create_app():
    from flask import Flask

    app = Flask(__name__, template_folder="templates")

    workspace_store = WorkspaceStore(Path(".mu_cli/workspaces"))
    uploads_root = Path(".mu_cli/uploads")
    base_tools: list[Tool] = [
        ReadFileTool(lambda: Path(workspace_store.snapshot.root) if workspace_store.snapshot else None),
        WriteFileTool(lambda: Path(workspace_store.snapshot.root) if workspace_store.snapshot else None),
        ApplyPatchTool(lambda: Path(workspace_store.snapshot.root) if workspace_store.snapshot else None),
        GitTool(lambda: Path(workspace_store.snapshot.root) if workspace_store.snapshot else None),
        FetchUrlContextTool(),
        FetchPdfContextTool(),
        ExtractLinksContextTool(),
        SearchWebContextTool(),
        SearchArxivPapersTool(),
        ScoreSourcesTool(),
        ListWorkspaceFilesTool(workspace_store),
        GetWorkspaceFileContextTool(workspace_store),
        MakefileAgentTool(lambda: Path(workspace_store.snapshot.root) if workspace_store.snapshot else None),
        ListUploadedContextFilesTool(uploads_root, lambda: runtime.session_name),
        GetUploadedContextFileTool(uploads_root, lambda: runtime.session_name),
        ClearUploadedContextStoreTool(uploads_root, lambda: runtime.session_name),
        RetrieveConversationSummaryTool(lambda: runtime),
    ]
    session_store = SessionStore(Path(".mu_cli/sessions"), "default")
    skill_store = SkillStore(Path("skills"))

    runtime = WebRuntime(
        provider="echo",
        model="echo",
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        google_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
        ollama_endpoint=os.getenv("OLLAMA_HOST"),
        approval_mode="ask",
        system_prompt="You are a helpful coding assistant. Keep responses concise.",
        session_name="default",
        workspace_path=None,
        debug=True,
        debug_level="info",
        agentic_planning=True,
        research_mode=False,
        workspace_store=workspace_store,
        session_store=session_store,
        pricing=PricingCatalog(Path(".mu_cli/pricing.json")),
        tools=list(base_tools),
        agent=Agent(provider=EchoProvider(), tools=list(base_tools)),
        traces=[],
        session_usage=_default_usage(),
        session_turns=[],
        uploads=[],
        uploads_dir=uploads_root,
        base_tools=base_tools,
        enabled_tools={tool.name: True for tool in base_tools},
        custom_tool_specs=[],
        custom_tool_errors=[],
        research_artifacts={},
        max_runtime_seconds=900,
        condense_enabled=False,
        condense_window=12,
        ollama_context_window=65536,
        summary_index=[],
        skill_store=skill_store,
        enabled_skills=[],
        telemetry={},
    )
    runtime.uploads_dir.mkdir(parents=True, exist_ok=True)
    _load_telemetry(runtime)
    _refresh_tooling(runtime)
    runtime.agent = _new_agent(runtime)
    runtime.agent.add_system_prompt(runtime.system_prompt)
    _inject_planning(runtime.agent, git_guidance=_git_agent_instruction(runtime))
    if runtime.research_mode:
        _inject_research_prompt(runtime.agent)
    _sync_skill_prompts(runtime)
    if not _load_session(runtime, runtime.session_name):
        _persist(runtime)


    @app.before_request
    def _telemetry_request_hook():
        from flask import g, request
        key = f"{request.method} {request.path}"
        _record_telemetry(runtime, 'request_counts', key)
        g._request_started_at = datetime.now(timezone.utc)
        if _should_log(runtime, "debug"):
            body = request.get_json(silent=True)
            if body is not None:
                _log_trace(runtime, "debug", f"incoming {request.method} {request.path} body={json.dumps(body, ensure_ascii=False)[:500]}")
            else:
                _log_trace(runtime, "debug", f"incoming {request.method} {request.path}")

    @app.after_request
    def _response_log_hook(response):
        from flask import g, request
        started = getattr(g, "_request_started_at", None)
        elapsed_ms = 0
        if started is not None:
            elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        level = "error" if response.status_code >= 500 else ("warn" if response.status_code >= 400 else "info")
        if _should_log(runtime, level):
            _log_trace(runtime, level, f"outgoing {request.method} {request.path} status={response.status_code} duration_ms={elapsed_ms}")
        return response

    runtime_mutation_deps = RuntimeMutationDeps(
        get_models=get_models,
        provider_api_key=_provider_api_key,
        attach_workspace_if_available=_attach_workspace_if_available,
        initialize_fresh_session_state=_initialize_fresh_session_state,
        initialize_fresh_session_state_reset_summary=lambda runtime_ref: _initialize_fresh_session_state(runtime_ref, reset_summary_index=True),
        refresh_tooling=_refresh_tooling,
        new_agent=_new_agent,
        inject_planning=_inject_planning,
        inject_research_prompt=_inject_research_prompt,
        sync_skill_prompts=_sync_skill_prompts,
        git_agent_instruction=_git_agent_instruction,
    )

    register_state_routes(
        app,
        runtime,
        StateRouteDeps(
            discover_git_repos=_discover_git_repos,
            is_git_repo=_is_git_repo,
            git_current_branch=_git_current_branch,
            git_branches=_git_branches,
            get_model_catalog=get_model_catalog,
            mutate_for_settings=lambda runtime_ref, payload: mutate_runtime_for_settings(runtime_ref, payload, runtime_mutation_deps),
            persist=_persist,
            remove_uploaded_entry=_remove_uploaded_entry,
            clear_all_stored_data=_clear_all_stored_data,
            telemetry_snapshot=_telemetry_snapshot,
            record_telemetry_action=lambda runtime_ref, action: _record_telemetry(runtime_ref, "action_counts", action),
        ),
    )

    register_chat_routes(
        app,
        runtime,
        ChatRouteDeps(
            record_telemetry_action=lambda runtime_ref, action: _record_telemetry(runtime_ref, "action_counts", action),
            run_turn_with_uploaded_context=_run_turn_with_uploaded_context,
            turn_report=_turn_report,
            record_turn=_record_turn,
            condense_session_context=_condense_session_context,
            persist=_persist,
            start_background_turn=_start_background_turn,
            iter_chunks=_iter_chunks,
            load_session=_load_session,
        ),
    )

    register_session_routes(
        app,
        runtime,
        SessionRouteDeps(
            record_telemetry_action=lambda runtime_ref, action: _record_telemetry(runtime_ref, "action_counts", action),
            load_session=_load_session,
            delete_session=lambda runtime_ref, name: runtime_ref.session_store.delete(name),
            persist=_persist,
            condense_session_context=_condense_session_context,
            mutate_for_new_session=lambda runtime_ref, payload, name: mutate_runtime_for_new_session(runtime_ref, payload, name, runtime_mutation_deps),
            mutate_for_clear=lambda runtime_ref, reset_summary_index: mutate_runtime_for_clear(runtime_ref, reset_summary_index=reset_summary_index, deps=runtime_mutation_deps),
        ),
    )

    return app


def main() -> None:
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
