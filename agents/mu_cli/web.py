from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field
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
from mu_cli.providers.openai import OpenAIProvider
from mu_cli.session import SessionState, SessionStore
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
    ReadFileTool,
    WriteFileTool,
)
from mu_cli.workspace import WorkspaceStore
from werkzeug.utils import secure_filename


@dataclass(slots=True)
class WebRuntime:
    provider: str
    model: str
    api_key: str | None
    approval_mode: str
    system_prompt: str
    session_name: str
    workspace_path: str | None
    debug: bool
    agentic_planning: bool
    research_mode: bool
    workspace_store: WorkspaceStore
    session_store: SessionStore
    pricing: PricingCatalog
    tools: list[Tool]
    agent: Agent
    traces: list[str]
    session_usage: dict[str, float]
    session_turns: list[dict]
    uploads: list[dict]
    uploads_dir: Path
    base_tools: list[Tool]
    enabled_tools: dict[str, bool]
    custom_tool_specs: list[dict]
    custom_tool_errors: list[str]
    research_artifacts: dict[str, Any]
    approval_condition: threading.Condition = field(default_factory=threading.Condition)
    pending_approval: dict[str, Any] | None = None
    background_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_runtime_seconds: int = 900
    condense_enabled: bool = False
    condense_window: int = 12
    summary_index: list[dict[str, Any]] = field(default_factory=list)




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
    return {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0, "estimated_cost_usd": 0.0}



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

def _build_provider(name: str, model: str, api_key: str | None):
    if name == "echo":
        return EchoProvider()
    if name == "openai":
        return OpenAIProvider(model=model, api_key=api_key)
    if name == "gemini":
        return GeminiProvider(model=model, api_key=api_key)
    raise ValueError(f"Unsupported provider: {name}")


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
    provider = _build_provider(runtime.provider, runtime.model, runtime.api_key)

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
            f"approval: id={request_id} tool={tool_name} decision={'approve' if approved else 'deny'}"
        )
        return approved

    def on_model_response(message: Message, calls: list[ToolCall]) -> None:
        if not runtime.debug:
            return
        runtime.traces.append(f"model: {message.content}")
        for call in calls:
            runtime.traces.append(f"tool-request: id={call.call_id} name={call.name} args={call.args}")

    def on_tool_run(name: str, args: dict, ok: bool, output: str) -> None:
        runtime.workspace_store.record_tool_run(name, args, output, ok)
        latency_ms = _extract_latency_ms(output)
        _update_tool_reliability(runtime, name, ok, latency_ms)
        if runtime.debug:
            latency_suffix = f" latency_ms={latency_ms}" if latency_ms is not None else ""
            runtime.traces.append(f"tool-run: name={name} ok={ok}{latency_suffix} args={args} output={output[:200]}")

    return Agent(
        provider=provider,
        tools=runtime.tools,
        on_approval=on_approval,
        on_model_response=on_model_response,
        on_tool_run=on_tool_run,
        strict_tool_usage=True,
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
    if len(non_system) <= raw_window + 2:
        return {"ok": True, "unchanged": True, "message": "not enough history to condense"}

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

    older_set = set(id(m) for m in older)
    for msg in runtime.agent.state.messages:
        if msg.role is Role.SYSTEM:
            continue
        if id(msg) in older_set:
            msg.metadata["excluded_from_model"] = True

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
    runtime.agent.state.messages.append(summary_msg)

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
            condense_enabled=runtime.condense_enabled,
            condense_window=runtime.condense_window,
            summary_index=runtime.summary_index,
        )
    )


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
    if loaded.condense_enabled is not None:
        runtime.condense_enabled = bool(loaded.condense_enabled)
    if loaded.condense_window is not None:
        runtime.condense_window = int(loaded.condense_window)
    runtime.summary_index = list(loaded.summary_index or [])
    if runtime.workspace_path:
        path = Path(runtime.workspace_path).expanduser()
        if path.exists() and path.is_dir():
            runtime.workspace_store.attach(path)

    if runtime.agentic_planning:
        summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
        _inject_planning(runtime.agent, summary, _git_agent_instruction(runtime))
    if runtime.research_mode:
        _inject_research_prompt(runtime.agent)

    return True




def _build_session_runtime(base: WebRuntime, session_name: str) -> WebRuntime:
    session_store = SessionStore(Path(".mu_cli/sessions"), session_name)
    workspace_store = WorkspaceStore(Path(".mu_cli/workspaces"))
    runtime = WebRuntime(
        provider=base.provider,
        model=base.model,
        api_key=base.api_key,
        approval_mode=base.approval_mode,
        system_prompt=base.system_prompt,
        session_name=session_name,
        workspace_path=None,
        debug=base.debug,
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
        summary_index=[],
    )
    _refresh_tooling(runtime)
    if not _load_session(runtime, session_name):
        runtime.agent = _new_agent(runtime)
        runtime.agent.add_system_prompt(runtime.system_prompt)
        if runtime.agentic_planning:
            _inject_planning(runtime.agent, git_guidance=_git_agent_instruction(runtime))
        if runtime.research_mode:
            _inject_research_prompt(runtime.agent)
        _persist(runtime)
    return runtime


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
        "planner_critic": None,
        "verification_policy": _verification_policy_for_task(text),
        "checkpoints": [],
        "answer_contract": None,
    }

    def runner() -> None:
        job = base_runtime.background_jobs[job_id]
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
                plan_reply = isolated.agent.step(
                    "Create an execution plan for the task below. Keep it short and actionable as numbered steps. "
                    "Start with 'PLAN:'.\n\nTask:\n" + text
                )
                plan_text = (plan_reply.content or "").strip()
                if not plan_text or plan_text.lower().startswith("calling tool"):
                    fallback_reply = isolated.agent.step(
                        "Provide ONLY a concise numbered execution plan for the same task. "
                        "Do not call tools. Start with 'PLAN:'."
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
                critic_reply = isolated.agent.step(critic_prompt)
                critic_text = (critic_reply.content or "").strip()
                plan_ok = "plan_ok: yes" in critic_text.lower()
                job["planner_critic"] = critic_text[:1200]
                job["events"].append("plan: critic_passed" if plan_ok else "plan: critic_failed")
                if not plan_ok:
                    revise_reply = isolated.agent.step(
                        "Revise the prior plan to address critique gaps. Return only a numbered plan starting with 'PLAN:'."
                    )
                    revised_text = (revise_reply.content or "").strip()
                    if revised_text:
                        plan_text = revised_text
                        job["events"].append("plan: revised_after_critic")
                job["plan"] = plan_text
                job["last_step"] = "Plan drafted; waiting for approval"
                job["events"].append("plan: drafted")
                _persist(isolated)
                job["status"] = "awaiting_plan_approval"
                while datetime.now(timezone.utc).timestamp() < deadline:
                    decision = job.get("plan_approval")
                    if decision in {"approve", "deny"}:
                        break
                    threading.Event().wait(0.4)
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
                    break
                if not isolated.agentic_planning:
                    break
                if not had_tool_activity:
                    no_progress_streak = no_progress_streak + 1 if stalled else 0
                    if no_progress_streak >= 2:
                        job["events"].append("status: stalled_no_tool_progress")
                        if replan_count < 2:
                            replan_count += 1
                            replan_reply = isolated.agent.step(
                                "Generate REPLAN with 3-6 concise steps to recover from stall. "
                                "Start with 'REPLAN:'. Include one immediate next tool call recommendation."
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

            if datetime.now(timezone.utc).timestamp() >= deadline:
                job["status"] = "timed_out"

            job["report"] = {
                "provider": isolated.provider,
                "model": isolated.model,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_tokens,
                "estimated_cost_usd": total_cost,
            }
            if job["status"] != "timed_out":
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
            if job.get("status") != "timed_out":
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
    from flask import Flask, Response, jsonify, render_template, request, stream_with_context

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
        ListUploadedContextFilesTool(uploads_root, lambda: runtime.session_name),
        GetUploadedContextFileTool(uploads_root, lambda: runtime.session_name),
        ClearUploadedContextStoreTool(uploads_root, lambda: runtime.session_name),
        RetrieveConversationSummaryTool(lambda: runtime),
    ]
    session_store = SessionStore(Path(".mu_cli/sessions"), "default")

    runtime = WebRuntime(
        provider="echo",
        model="echo",
        api_key=None,
        approval_mode="ask",
        system_prompt="You are a helpful coding assistant. Keep responses concise.",
        session_name="default",
        workspace_path=None,
        debug=False,
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
        summary_index=[],
    )
    runtime.uploads_dir.mkdir(parents=True, exist_ok=True)
    _refresh_tooling(runtime)
    runtime.agent = _new_agent(runtime)
    runtime.agent.add_system_prompt(runtime.system_prompt)
    _inject_planning(runtime.agent, git_guidance=_git_agent_instruction(runtime))
    if runtime.research_mode:
        _inject_research_prompt(runtime.agent)
    if not _load_session(runtime, runtime.session_name):
        _persist(runtime)

    def _ui_messages() -> list[dict]:
        return [asdict(m) for m in runtime.agent.state.messages if m.role is not Role.SYSTEM]

    @app.get("/")
    def index():
        return render_template("index_htmx.html")

    @app.get("/legacy")
    def index_legacy():
        return render_template("index.html")

    @app.get("/ui/messages")
    def ui_messages():
        return render_template("partials/messages.html", messages=_ui_messages())

    @app.post("/ui/chat")
    def ui_chat():
        text = str(request.form.get("text", "")).strip()
        if text:
            reply = _run_turn_with_uploaded_context(runtime, text)
            report = _turn_report(runtime, text, reply.content)
            _record_turn(runtime, report)
            if runtime.condense_enabled:
                _condense_session_context(runtime, window_size=runtime.condense_window)
            _persist(runtime)
        return render_template("partials/messages.html", messages=_ui_messages())


    @app.get("/ui/state")
    def ui_state():
        return render_template(
            "partials/state.html",
            provider=runtime.provider,
            model=runtime.model,
            approval_mode=runtime.approval_mode,
            session=runtime.session_name,
            workspace=runtime.workspace_path,
            debug=runtime.debug,
            traces=runtime.traces[-20:],
        )



    def _session_statuses() -> dict[str, str]:
        statuses: dict[str, str] = {name: "idle" for name in runtime.session_store.list_sessions()}
        statuses.setdefault(runtime.session_name, "idle")
        for job in runtime.background_jobs.values():
            if not isinstance(job, dict):
                continue
            sname = str(job.get("session", "")).strip()
            if not sname:
                continue
            state = str(job.get("status", "")).strip().lower()
            if state in {"running", "waiting_plan"}:
                statuses[sname] = "running"
            elif statuses.get(sname) != "running" and state in {"failed", "timed_out"}:
                statuses[sname] = "attention"
        return statuses

    @app.get("/ui/session")
    def ui_session():
        return render_template(
            "partials/session.html",
            active=runtime.session_name,
            sessions=runtime.session_store.list_sessions(),
            statuses=_session_statuses(),
            message=request.args.get("message", ""),
        )

    @app.post("/ui/session")
    def ui_session_action():
        action = str(request.form.get("action", "")).strip()
        raw_name = str(request.form.get("name", "")).strip()
        name = raw_name or runtime.session_name
        message = ""
        if action == "load":
            if _load_session(runtime, name):
                message = f"loaded {name}"
            else:
                message = "session not found"
        elif action == "new":
            if not raw_name:
                name = f"session-{int(time.time())}"
            else:
                name = raw_name
            runtime.session_name = name
            runtime.session_store.use(name)
            runtime.agent = _new_agent(runtime)
            runtime.agent.add_system_prompt(runtime.system_prompt)
            _persist(runtime)
            message = f"created {name}"
        elif action == "delete":
            if name == runtime.session_name:
                message = "cannot delete active"
            elif runtime.session_store.delete(name):
                message = f"deleted {name}"
            else:
                message = "session not found"
        return render_template(
            "partials/session.html",
            active=runtime.session_name,
            sessions=runtime.session_store.list_sessions(),
            statuses=_session_statuses(),
            message=message,
        )

    @app.get("/ui/jobs")
    def ui_jobs():
        return render_template("partials/jobs.html", jobs=list(runtime.background_jobs.values())[-20:])

    @app.post("/ui/chat/background")
    def ui_chat_background():
        text = str(request.form.get("text", "")).strip()
        if text:
            _start_background_turn(runtime, runtime.session_name, text)
        return render_template("partials/jobs.html", jobs=list(runtime.background_jobs.values())[-20:])

    @app.get("/ui/settings")
    def ui_settings():
        variant = str(request.args.get("variant", "full")).strip().lower()
        target_id = "#settings-modal-content" if variant == "full" else "#settings-panel"
        provider = str(request.args.get("provider", runtime.provider)).strip() or runtime.provider
        provider_models = get_models(provider, runtime.api_key)
        selected_model = str(request.args.get("model", runtime.model)).strip() or runtime.model
        if selected_model not in provider_models and provider_models:
            selected_model = provider_models[0]
        return render_template(
            "partials/settings.html",
            provider=provider,
            model=selected_model,
            models=provider_models,
            approval_mode=runtime.approval_mode,
            workspace=runtime.workspace_path or "",
            debug=runtime.debug,
            agentic_planning=runtime.agentic_planning,
            research_mode=runtime.research_mode,
            max_runtime_seconds=runtime.max_runtime_seconds,
            condense_enabled=runtime.condense_enabled,
            condense_window=runtime.condense_window,
            variant=variant,
            target_id=target_id,
        )

    @app.post("/ui/settings")
    def ui_update_settings():
        form = request.form
        variant = str(form.get("variant", "full")).strip().lower()
        target_id = "#settings-modal-content" if variant == "full" else "#settings-panel"

        runtime.provider = str(form.get("provider", runtime.provider)).strip() or runtime.provider
        selected_model = str(form.get("model", runtime.model)).strip() or runtime.model
        available = get_models(runtime.provider, runtime.api_key)
        runtime.model = selected_model if selected_model in available else (available[0] if available else runtime.model)
        runtime.approval_mode = str(form.get("approval_mode", runtime.approval_mode)).strip() or runtime.approval_mode

        workspace = str(form.get("workspace", "")).strip()
        if workspace:
            path = Path(workspace).expanduser()
            if path.exists() and path.is_dir():
                snapshot = runtime.workspace_store.attach(path)
                runtime.workspace_path = str(path)
                runtime.traces.append(f"workspace-attached: {snapshot.root} files={len(snapshot.files)}")

        runtime.debug = str(form.get("debug", "")).lower() in {"on", "true", "1", "yes"}
        runtime.agentic_planning = str(form.get("agentic_planning", "")).lower() in {"on", "true", "1", "yes"}
        runtime.research_mode = str(form.get("research_mode", "")).lower() in {"on", "true", "1", "yes"}

        max_runtime_val = str(form.get("max_runtime_seconds", runtime.max_runtime_seconds)).strip()
        if max_runtime_val:
            runtime.max_runtime_seconds = int(max_runtime_val)

        runtime.condense_enabled = str(form.get("condense_enabled", "")).lower() in {"on", "true", "1", "yes"}
        condense_window_val = str(form.get("condense_window", runtime.condense_window)).strip()
        if condense_window_val:
            runtime.condense_window = int(condense_window_val)

        previous_messages = list(runtime.agent.state.messages)
        _refresh_tooling(runtime)
        runtime.agent = _new_agent(runtime)
        runtime.agent.state.messages = previous_messages
        if runtime.agentic_planning:
            summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
            _inject_planning(runtime.agent, summary, _git_agent_instruction(runtime))
        if runtime.research_mode:
            _inject_research_prompt(runtime.agent)
        _persist(runtime)

        provider_models = get_models(runtime.provider, runtime.api_key)
        if runtime.model not in provider_models and provider_models:
            provider_models = [runtime.model] + provider_models
        return render_template(
            "partials/settings.html",
            provider=runtime.provider,
            model=runtime.model,
            models=provider_models,
            approval_mode=runtime.approval_mode,
            workspace=runtime.workspace_path or "",
            debug=runtime.debug,
            agentic_planning=runtime.agentic_planning,
            research_mode=runtime.research_mode,
            max_runtime_seconds=runtime.max_runtime_seconds,
            condense_enabled=runtime.condense_enabled,
            condense_window=runtime.condense_window,
            variant=variant,
            target_id=target_id,
            saved=True,
        )

    @app.get("/api/state")
    def state():
        sessions = runtime.session_store.list_sessions()
        git_repos: list[str] = []
        git_current_repo: str | None = None
        git_current_branch: str | None = None
        git_branches: list[str] = []
        if runtime.workspace_path:
            workspace = Path(runtime.workspace_path).expanduser()
            git_repos = _discover_git_repos(workspace)
            if _is_git_repo(workspace):
                git_current_repo = str(workspace)
            elif git_repos:
                git_current_repo = git_repos[0]
            if git_current_repo:
                repo_path = Path(git_current_repo)
                git_current_branch = _git_current_branch(repo_path)
                git_branches = _git_branches(repo_path)
        return jsonify(
            {
                "provider": runtime.provider,
                "model": runtime.model,
                "approval_mode": runtime.approval_mode,
                "session": runtime.session_name,
                "workspace": runtime.workspace_path,
                "debug": runtime.debug,
                "agentic_planning": runtime.agentic_planning,
                "research_mode": runtime.research_mode,
                "models": get_model_catalog({"gemini": runtime.api_key}),
                "sessions": sessions,
                "messages": [asdict(m) for m in runtime.agent.state.messages if m.role is not Role.SYSTEM],
                "traces": runtime.traces[-50:],
                "session_usage": runtime.session_usage,
                "session_turns": runtime.session_turns[-200:],
                "pricing": runtime.pricing.data,
                "uploads": runtime.uploads,
                "pending_approval": runtime.pending_approval,
                "research_artifacts": runtime.research_artifacts,
                "background_jobs": list(runtime.background_jobs.values())[-50:],
                "max_runtime_seconds": runtime.max_runtime_seconds,
                "condense_enabled": runtime.condense_enabled,
                "condense_window": runtime.condense_window,
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "enabled": runtime.enabled_tools.get(tool.name, True),
                        "source": "builtin",
                    }
                    for tool in runtime.base_tools
                ]
                + [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "enabled": True,
                        "source": "custom",
                    }
                    for tool in runtime.tools
                    if tool.name not in {base.name for base in runtime.base_tools}
                ],
                "custom_tool_specs": runtime.custom_tool_specs,
                "custom_tool_errors": runtime.custom_tool_errors,
                "git_repos": git_repos,
                "git_current_repo": git_current_repo,
                "git_current_branch": git_current_branch,
                "git_branches": git_branches,
            }
        )

    @app.post("/api/chat")
    def chat():
        payload = request.get_json(force=True)
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required"}), 400

        reply = _run_turn_with_uploaded_context(runtime, text)
        report = _turn_report(runtime, text, reply.content)
        _record_turn(runtime, report)
        if runtime.condense_enabled:
            _condense_session_context(runtime, window_size=runtime.condense_window)
        _persist(runtime)
        return jsonify({"reply": asdict(reply), "report": report, "traces": runtime.traces[-50:]})

    @app.post("/api/chat/background")
    def chat_background():
        payload = request.get_json(force=True)
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required"}), 400
        session_name = str(payload.get("session", runtime.session_name)).strip() or runtime.session_name
        job_id = _start_background_turn(runtime, session_name, text)
        return jsonify({"ok": True, "job_id": job_id, "session": session_name})

    @app.get("/api/jobs")
    def list_jobs():
        return jsonify({"jobs": list(runtime.background_jobs.values())})

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str):
        job = runtime.background_jobs.get(job_id)
        if job is None:
            return jsonify({"error": "job not found"}), 404
        return jsonify(job)

    @app.post("/api/jobs/<job_id>/plan")
    def decide_job_plan(job_id: str):
        job = runtime.background_jobs.get(job_id)
        if job is None:
            return jsonify({"error": "job not found"}), 404
        payload = request.get_json(force=True)
        decision = str(payload.get("decision", "")).strip().lower()
        if decision not in {"approve", "deny"}:
            return jsonify({"error": "decision must be approve|deny"}), 400
        revised_plan = str(payload.get("revised_plan", "")).strip()
        if decision == "approve" and revised_plan:
            job["plan"] = revised_plan
            events = job.setdefault("events", [])
            if isinstance(events, list):
                events.append("plan: revised_by_user")
        job["plan_approval"] = decision
        return jsonify({"ok": True, "job_id": job_id, "decision": decision, "plan": job.get("plan")})

    @app.post("/api/chat/stream")
    def chat_stream():
        payload = request.get_json(force=True)
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required"}), 400

        events: queue.Queue[dict] = queue.Queue()
        done = threading.Event()

        original_model_response = runtime.agent.on_model_response
        original_tool_run = runtime.agent.on_tool_run

        def stream_model_response(message: Message, calls: list[ToolCall]) -> None:
            if original_model_response is not None:
                original_model_response(message, calls)
            for call in calls:
                events.put({"type": "trace", "line": f"tool-request: id={call.call_id} name={call.name} args={call.args}"})
            if message.content:
                for chunk in _iter_chunks(message.content):
                    events.put({"type": "assistant_chunk", "chunk": chunk})

        def stream_tool_run(name: str, args: dict, ok: bool, output: str) -> None:
            if original_tool_run is not None:
                original_tool_run(name, args, ok, output)
            events.put({"type": "trace", "line": f"tool-run: name={name} ok={ok} args={args} output={output[:200]}"})

        def run_turn() -> None:
            runtime.agent.on_model_response = stream_model_response
            runtime.agent.on_tool_run = stream_tool_run
            try:
                reply = _run_turn_with_uploaded_context(runtime, text)
                report = _turn_report(runtime, text, reply.content)
                _record_turn(runtime, report)
                if runtime.condense_enabled:
                    _condense_session_context(runtime, window_size=runtime.condense_window)
                _persist(runtime)
                events.put({"type": "report", "report": report})
                events.put({"type": "done", "reply": asdict(reply), "traces": runtime.traces[-50:]})
            except Exception as exc:  # pragma: no cover - defensive for stream transport
                events.put({"type": "error", "error": str(exc)})
            finally:
                runtime.agent.on_model_response = original_model_response
                runtime.agent.on_tool_run = original_tool_run
                done.set()

        thread = threading.Thread(target=run_turn, daemon=True)
        thread.start()

        @stream_with_context
        def generate():
            while not done.is_set() or not events.empty():
                try:
                    item = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                yield json.dumps(item) + "\n"

        return Response(generate(), mimetype="application/x-ndjson")

    @app.post("/api/settings")
    def update_settings():
        payload = request.get_json(force=True)

        runtime.provider = str(payload.get("provider", runtime.provider))
        selected_model = str(payload.get("model", runtime.model))
        available = get_models(runtime.provider, runtime.api_key)
        runtime.model = selected_model if selected_model in available else (available[0] if available else runtime.model)
        runtime.api_key = payload.get("api_key", runtime.api_key)
        runtime.approval_mode = str(payload.get("approval_mode", runtime.approval_mode))
        runtime.debug = bool(payload.get("debug", runtime.debug))
        runtime.agentic_planning = bool(payload.get("agentic_planning", runtime.agentic_planning))
        runtime.research_mode = bool(payload.get("research_mode", runtime.research_mode))
        runtime.max_runtime_seconds = int(payload.get("max_runtime_seconds", runtime.max_runtime_seconds) or runtime.max_runtime_seconds)
        runtime.condense_enabled = bool(payload.get("condense_enabled", runtime.condense_enabled))
        runtime.condense_window = int(payload.get("condense_window", runtime.condense_window) or runtime.condense_window)
        tool_visibility = payload.get("tool_visibility")
        if isinstance(tool_visibility, dict):
            for tool in runtime.base_tools:
                value = tool_visibility.get(tool.name)
                if isinstance(value, bool):
                    runtime.enabled_tools[tool.name] = value

        custom_tools = payload.get("custom_tools")
        if isinstance(custom_tools, list):
            runtime.custom_tool_specs = custom_tools

        workspace = payload.get("workspace")
        if workspace:
            path = Path(str(workspace)).expanduser()
            if path.exists() and path.is_dir():
                snapshot = runtime.workspace_store.attach(path)
                runtime.workspace_path = str(path)
                runtime.traces.append(f"workspace-attached: {snapshot.root} files={len(snapshot.files)}")

        previous_messages = list(runtime.agent.state.messages)
        _refresh_tooling(runtime)
        runtime.agent = _new_agent(runtime)
        runtime.agent.state.messages = previous_messages
        if runtime.agentic_planning:
            summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
            _inject_planning(runtime.agent, summary, _git_agent_instruction(runtime))
        if runtime.research_mode:
            _inject_research_prompt(runtime.agent)

        _persist(runtime)
        return jsonify({"ok": True})

    @app.route("/api/pricing", methods=["GET", "POST"])
    def pricing_settings():
        if request.method == "GET":
            return jsonify({"pricing": runtime.pricing.data})

        payload = request.get_json(force=True)
        provider = str(payload.get("provider", "")).strip()
        model = str(payload.get("model", "")).strip()
        if not provider or not model:
            return jsonify({"error": "provider and model are required"}), 400

        input_per_1m = float(payload.get("input_per_1m", 0.0))
        output_per_1m = float(payload.get("output_per_1m", 0.0))
        runtime.pricing.update_model_pricing(provider, model, input_per_1m, output_per_1m)
        return jsonify({"ok": True, "pricing": runtime.pricing.data})

    @app.get("/api/fs/dirs")
    def list_dirs():
        raw = str(request.args.get("path", "") or "")
        path = Path(raw).expanduser() if raw else Path.cwd()
        if not path.exists() or not path.is_dir():
            return jsonify({"error": "invalid directory"}), 400

        children = []
        for child in sorted(path.iterdir(), key=lambda x: x.name.lower()):
            if child.is_dir() and not child.name.startswith('.'):
                children.append({"name": child.name, "path": str(child)})

        return jsonify(
            {
                "cwd": str(path),
                "parent": str(path.parent) if path.parent != path else None,
                "children": children,
            }
        )

    @app.get("/api/git/repos")
    def list_git_repos():
        raw_workspace = str(request.args.get("workspace", "") or "").strip()
        if not raw_workspace:
            return jsonify({"repos": []})
        workspace = Path(raw_workspace).expanduser()
        return jsonify({"repos": _discover_git_repos(workspace)})

    @app.get("/api/git/branches")
    def list_git_branches():
        raw_repo = str(request.args.get("repo", "") or "").strip()
        if not raw_repo:
            return jsonify({"error": "repo is required"}), 400
        repo = Path(raw_repo).expanduser()
        if not _is_git_repo(repo):
            return jsonify({"error": "repo is not a git repository"}), 400
        return jsonify({"repo": str(repo), "current_branch": _git_current_branch(repo), "branches": _git_branches(repo)})

    @app.post("/api/git/branch")
    def git_branch_action():
        payload = request.get_json(force=True)
        action = str(payload.get("action", "")).strip()
        raw_repo = str(payload.get("repo", "")).strip()
        if not raw_repo:
            return jsonify({"error": "repo is required"}), 400
        repo = Path(raw_repo).expanduser()
        if not _is_git_repo(repo):
            return jsonify({"error": "repo is not a git repository"}), 400

        if action == "create":
            branch = str(payload.get("branch", "")).strip()
            base = str(payload.get("base", "")).strip()
            if not branch:
                return jsonify({"error": "branch is required"}), 400
            cmd = ["git", "-C", str(repo), "checkout", "-b", branch]
            if base:
                cmd.append(base)
            proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
        elif action == "switch":
            branch = str(payload.get("branch", "")).strip()
            if not branch:
                return jsonify({"error": "branch is required"}), 400
            proc = subprocess.run(["git", "-C", str(repo), "checkout", branch], text=True, capture_output=True, check=False)
        else:
            return jsonify({"error": "action must be create|switch"}), 400

        if proc.returncode != 0:
            return jsonify({"error": (proc.stderr or proc.stdout or "git command failed").strip()}), 400
        return jsonify(
            {
                "ok": True,
                "repo": str(repo),
                "current_branch": _git_current_branch(repo),
                "branches": _git_branches(repo),
                "output": (proc.stdout or "").strip(),
            }
        )

    @app.get("/api/git/diff")
    def git_diff_status():
        raw_repo = str(request.args.get("repo", "") or "").strip()
        if not raw_repo:
            return jsonify({"error": "repo is required"}), 400
        repo = Path(raw_repo).expanduser()
        if not _is_git_repo(repo):
            return jsonify({"error": "repo is not a git repository"}), 400

        status_proc = subprocess.run(
            ["git", "-C", str(repo), "status", "--short"],
            text=True,
            capture_output=True,
            check=False,
        )
        diff_proc = subprocess.run(
            ["git", "-C", str(repo), "diff"],
            text=True,
            capture_output=True,
            check=False,
        )
        cached_diff_proc = subprocess.run(
            ["git", "-C", str(repo), "diff", "--cached"],
            text=True,
            capture_output=True,
            check=False,
        )
        if status_proc.returncode != 0 or diff_proc.returncode != 0 or cached_diff_proc.returncode != 0:
            return jsonify({"error": "unable to read git diff/status"}), 400

        return jsonify(
            {
                "repo": str(repo),
                "status": (status_proc.stdout or "").strip(),
                "diff": (diff_proc.stdout or "").strip(),
                "cached_diff": (cached_diff_proc.stdout or "").strip(),
            }
        )

    @app.route("/api/approval", methods=["GET", "POST"])
    def approval_actions():
        if request.method == "GET":
            return jsonify({"pending": runtime.pending_approval})

        payload = request.get_json(force=True)
        request_id = str(payload.get("id", "")).strip()
        decision = str(payload.get("decision", "")).strip().lower()
        if decision not in {"approve", "deny"}:
            return jsonify({"error": "decision must be approve|deny"}), 400

        with runtime.approval_condition:
            if runtime.pending_approval is None or runtime.pending_approval.get("id") != request_id:
                return jsonify({"error": "no matching pending approval"}), 404
            runtime.pending_approval["decision"] = decision
            runtime.approval_condition.notify_all()

        return jsonify({"ok": True})

    @app.post("/api/uploads")
    def upload_files():
        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "no files uploaded"}), 400

        session_dir = runtime.uploads_dir / runtime.session_name
        session_dir.mkdir(parents=True, exist_ok=True)
        uploaded: list[dict] = []

        for file in files:
            filename = secure_filename(file.filename or "upload.bin")
            if not filename:
                continue
            target = session_dir / filename
            file.save(target)

            raw = target.read_bytes()
            kind = "binary"
            try:
                raw.decode("utf-8")
                kind = "text"
            except UnicodeDecodeError:
                if target.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                    kind = "image"

            item = {
                "name": filename,
                "path": str(target),
                "size": len(raw),
                "kind": kind,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
            runtime.uploads.append(item)
            uploaded.append(item)

        _persist(runtime)
        return jsonify({"ok": True, "uploads": uploaded})

    @app.delete("/api/uploads")
    def clear_uploads():
        session_dir = runtime.uploads_dir / runtime.session_name
        removed = 0
        if session_dir.exists():
            for item in session_dir.iterdir():
                if item.is_file():
                    item.unlink()
                    removed += 1
        runtime.uploads = []
        _persist(runtime)
        return jsonify({"ok": True, "removed": removed})

    @app.delete("/api/uploads/<name>")
    def delete_upload(name: str):
        safe_name = Path(name).name
        session_dir = runtime.uploads_dir / runtime.session_name
        target = session_dir / safe_name
        if not target.exists() or not target.is_file():
            return jsonify({"error": "uploaded file not found"}), 404

        target.unlink()
        _remove_uploaded_entry(runtime, safe_name)
        _persist(runtime)
        return jsonify({"ok": True, "removed": safe_name})

    @app.get("/api/research/export")
    def export_research():
        fmt = str(request.args.get("format", "json")).strip().lower()
        artifacts = runtime.research_artifacts or {}
        if fmt == "markdown" or fmt == "md":
            lines = ["# Research Artifacts", ""]
            lines.append("## Visited URLs")
            for url in artifacts.get("visited_urls", []):
                lines.append(f"- {url}")
            lines.append("")
            lines.append("## Deduped Sources")
            for item in artifacts.get("deduped_sources", []):
                lines.append(f"- {item.get('url','')} (count={item.get('count', 0)})")
            lines.append("")
            lines.append("## Claim Graph")
            for claim, urls in artifacts.get("claim_graph", {}).items():
                lines.append(f"- {claim}")
                for url in urls:
                    lines.append(f"  - {url}")
            return jsonify({"format": "markdown", "content": "\n".join(lines)})
        return jsonify({"format": "json", "content": artifacts})


    @app.post("/api/session")
    def session_action():
        payload = request.get_json(force=True)
        action = str(payload.get("action", "")).strip()
        name = str(payload.get("name", "")).strip()

        if action == "status":
            return jsonify({"session": runtime.session_name})

        if action == "list":
            return jsonify({"sessions": runtime.session_store.list_sessions()})

        if action == "new":
            if not name:
                return jsonify({"error": "name required"}), 400

            runtime.provider = str(payload.get("provider", runtime.provider))
            selected_model = str(payload.get("model", runtime.model))
            runtime.api_key = payload.get("api_key", runtime.api_key)
            available = get_models(runtime.provider, runtime.api_key)
            runtime.model = selected_model if selected_model in available else (available[0] if available else runtime.model)
            runtime.agentic_planning = bool(payload.get("agentic_planning", runtime.agentic_planning))
            runtime.research_mode = bool(payload.get("research_mode", runtime.research_mode))
            runtime.approval_mode = str(payload.get("approval_mode", runtime.approval_mode))
            runtime.max_runtime_seconds = int(payload.get("max_runtime_seconds", runtime.max_runtime_seconds) or runtime.max_runtime_seconds)
            runtime.condense_enabled = bool(payload.get("condense_enabled", runtime.condense_enabled))
            runtime.condense_window = int(payload.get("condense_window", runtime.condense_window) or runtime.condense_window)

            workspace = payload.get("workspace")
            runtime.workspace_path = str(workspace).strip() if workspace else None
            runtime.workspace_store.snapshot = None
            if runtime.workspace_path:
                path = Path(runtime.workspace_path).expanduser()
                if path.exists() and path.is_dir():
                    runtime.workspace_store.attach(path)

            runtime.session_name = name
            runtime.session_store.use(name)
            runtime.agent = _new_agent(runtime)
            runtime.agent.add_system_prompt(runtime.system_prompt)
            runtime.session_usage = _default_usage()
            runtime.session_turns = []
            runtime.uploads = []
            runtime.research_artifacts = {}
            if runtime.agentic_planning:
                summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
                _inject_planning(runtime.agent, summary, _git_agent_instruction(runtime))
            if runtime.research_mode:
                _inject_research_prompt(runtime.agent)
            _persist(runtime)
            return jsonify({"ok": True, "session": name})

        if action in {"load", "switch"}:
            if not name:
                return jsonify({"error": "name required"}), 400
            loaded = _load_session(runtime, name)
            if not loaded:
                return jsonify({"error": "session not found"}), 404
            return jsonify({"ok": True, "session": name})

        if action == "delete":
            if not name:
                return jsonify({"error": "name required"}), 400
            if name == runtime.session_name:
                return jsonify({"error": "cannot delete active session"}), 400
            deleted = runtime.session_store.delete(name)
            if not deleted:
                return jsonify({"error": "session not found"}), 404
            return jsonify({"ok": True})

        if action == "condense":
            w = payload.get("window")
            result = _condense_session_context(runtime, window_size=int(w) if w is not None else runtime.condense_window)
            _persist(runtime)
            return jsonify(result)

        return jsonify({"error": "unsupported action"}), 400

    return app


def main() -> None:
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
