from __future__ import annotations

import json
import queue
import threading
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from mu_cli.agent import Agent
from mu_cli.cli import PLANNING_PROMPT_BASE
from mu_cli.core.types import Message, Role, ToolCall, UsageStats
from mu_cli.models import MODELS_BY_PROVIDER, get_models
from mu_cli.pricing import PricingCatalog, estimate_tokens
from mu_cli.providers.echo import EchoProvider
from mu_cli.providers.gemini import GeminiProvider
from mu_cli.providers.openai import OpenAIProvider
from mu_cli.session import SessionState, SessionStore
from mu_cli.tools.base import Tool
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
    api_keys: dict[str, str | None]
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


def _default_usage() -> dict[str, float]:
    return {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0, "estimated_cost_usd": 0.0}


def _build_provider(name: str, model: str, api_key: str | None):
    if name == "echo":
        return EchoProvider()
    if name == "openai":
        return OpenAIProvider(model=model, api_key=api_key)
    if name == "gemini":
        return GeminiProvider(model=model, api_key=api_key)
    raise ValueError(f"Unsupported provider: {name}")


def _provider_api_key(runtime: WebRuntime, provider: str | None = None) -> str | None:
    current = provider or runtime.provider
    keys = runtime.api_keys or {}
    scoped = keys.get(current)
    if isinstance(scoped, str) and scoped.strip():
        return scoped.strip()
    return runtime.api_key


def _planning_prompt(workspace_summary: str | None = None) -> str:
    if workspace_summary:
        return f"{PLANNING_PROMPT_BASE} Workspace context: {workspace_summary}"
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


def _inject_planning(agent: Agent, workspace_summary: str | None = None) -> None:
    already = any(
        message.role is Role.SYSTEM and message.metadata.get("kind") == "agentic_planning"
        for message in agent.state.messages
    )
    if already:
        return
    agent.state.messages.append(
        Message(
            role=Role.SYSTEM,
            content=_planning_prompt(workspace_summary),
            metadata={"kind": "agentic_planning"},
        )
    )


def _new_agent(runtime: WebRuntime) -> Agent:
    provider = _build_provider(runtime.provider, runtime.model, _provider_api_key(runtime, runtime.provider))

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
        if runtime.debug:
            runtime.traces.append(f"tool-run: name={name} ok={ok} args={args} output={output[:200]}")

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


def _condense_session_context(runtime: WebRuntime) -> dict[str, Any]:
    non_system = [m for m in runtime.agent.state.messages if m.role is not Role.SYSTEM]
    if len(non_system) < 6:
        return {"ok": True, "unchanged": True, "message": "not enough history to condense"}

    users = [m for m in non_system if m.role is Role.USER]
    assistants = [m for m in non_system if m.role is Role.ASSISTANT]
    tool_events = [m for m in non_system if m.role in {Role.TOOL_CALL, Role.TOOL_RESULT}]
    recent = non_system[-8:]

    highlights: list[str] = []
    for m in non_system:
        text = " ".join(str(m.content or "").split())
        if not text:
            continue
        if len(text) > 140:
            text = f"{text[:137]}..."
        prefix = "user" if m.role is Role.USER else "assistant" if m.role is Role.ASSISTANT else "tool"
        highlights.append(f"- [{prefix}] {text}")
        if len(highlights) >= 10:
            break

    summary_lines = [
        "Session condensed summary:",
        f"- total chat messages before condense: {len(non_system)}",
        f"- user turns: {len(users)}",
        f"- assistant turns: {len(assistants)}",
        f"- tool events: {len(tool_events)}",
        "- key highlights:",
        *(highlights or ["- (no textual highlights)"]),
        "- note: recent messages are preserved below this summary.",
    ]

    summary_msg = Message(
        role=Role.ASSISTANT,
        content="\n".join(summary_lines),
        metadata={"kind": "session_condensed_summary", "timestamp": datetime.now(timezone.utc).isoformat()},
    )

    system_messages = [m for m in runtime.agent.state.messages if m.role is Role.SYSTEM]
    runtime.agent.state.messages = [*system_messages, summary_msg, *recent]

    return {
        "ok": True,
        "condensed": True,
        "before": len(non_system),
        "after": len([m for m in runtime.agent.state.messages if m.role is not Role.SYSTEM]),
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


def _run_turn_with_uploaded_context(runtime: WebRuntime, text: str) -> Message:
    uploaded_prompt = _uploaded_context_prompt(runtime)
    before = len(runtime.agent.state.messages)
    if not uploaded_prompt:
        reply = runtime.agent.step(text)
        turn_messages = runtime.agent.state.messages[before:]
        ok, reason = _validate_claim_citations(turn_messages)
        if runtime.research_mode and not ok:
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
        if runtime.research_mode and not ok:
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
            api_keys=runtime.api_keys,
            usage_totals=runtime.session_usage,
            turns=runtime.session_turns,
            uploads=runtime.uploads,
            research_artifacts=runtime.research_artifacts,
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
    runtime.api_keys = dict(loaded.api_keys or runtime.api_keys or {})
    runtime.api_key = _provider_api_key(runtime, runtime.provider)
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
    if runtime.workspace_path:
        path = Path(runtime.workspace_path).expanduser()
        if path.exists() and path.is_dir():
            runtime.workspace_store.attach(path)

    if runtime.agentic_planning:
        summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
        _inject_planning(runtime.agent, summary)
    if runtime.research_mode:
        _inject_research_prompt(runtime.agent)

    return True


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
    ]
    session_store = SessionStore(Path(".mu_cli/sessions"), "default")

    runtime = WebRuntime(
        provider="echo",
        model="echo",
        api_key=None,
        api_keys={"openai": None, "gemini": None, "echo": None},
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
    )
    runtime.uploads_dir.mkdir(parents=True, exist_ok=True)
    _refresh_tooling(runtime)
    runtime.agent = _new_agent(runtime)
    runtime.agent.add_system_prompt(runtime.system_prompt)
    _inject_planning(runtime.agent)
    if runtime.research_mode:
        _inject_research_prompt(runtime.agent)
    if not _load_session(runtime, runtime.session_name):
        _persist(runtime)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def state():
        sessions = runtime.session_store.list_sessions()
        return jsonify(
            {
                "provider": runtime.provider,
                "model": runtime.model,
                "approval_mode": runtime.approval_mode,
                "api_keys": runtime.api_keys,
                "session": runtime.session_name,
                "workspace": runtime.workspace_path,
                "debug": runtime.debug,
                "agentic_planning": runtime.agentic_planning,
                "research_mode": runtime.research_mode,
                "models": MODELS_BY_PROVIDER,
                "sessions": sessions,
                "messages": [asdict(m) for m in runtime.agent.state.messages if m.role is not Role.SYSTEM],
                "traces": runtime.traces[-50:],
                "session_usage": runtime.session_usage,
                "session_turns": runtime.session_turns[-200:],
                "pricing": runtime.pricing.data,
                "uploads": runtime.uploads,
                "pending_approval": runtime.pending_approval,
                "research_artifacts": runtime.research_artifacts,
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
        _persist(runtime)
        return jsonify({"reply": asdict(reply), "report": report, "traces": runtime.traces[-50:]})

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
        available = get_models(runtime.provider)
        runtime.model = selected_model if selected_model in available else (available[0] if available else runtime.model)
        api_keys_payload = payload.get("api_keys")
        if isinstance(api_keys_payload, dict):
            merged = dict(runtime.api_keys or {})
            for key in ("openai", "gemini", "echo"):
                value = api_keys_payload.get(key, merged.get(key))
                if isinstance(value, str):
                    merged[key] = value.strip() or None
                elif value is None:
                    merged[key] = None
            runtime.api_keys = merged
        elif "api_key" in payload:
            current = runtime.provider
            value = payload.get("api_key")
            if value is None:
                runtime.api_keys[current] = None
            else:
                runtime.api_keys[current] = str(value).strip() or None
        runtime.api_key = _provider_api_key(runtime, runtime.provider)
        runtime.approval_mode = str(payload.get("approval_mode", runtime.approval_mode))
        runtime.debug = bool(payload.get("debug", runtime.debug))
        runtime.agentic_planning = bool(payload.get("agentic_planning", runtime.agentic_planning))
        runtime.research_mode = bool(payload.get("research_mode", runtime.research_mode))
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
        try:
            runtime.agent = _new_agent(runtime)
        except ValueError as exc:
            return jsonify({
                "error": (
                    f"API key required for {runtime.provider}:{runtime.model}. "
                    "Please update the provider API key in Advanced settings."
                ) if "API_KEY" in str(exc) or "api key" in str(exc).lower() else str(exc)
            }), 400
        runtime.agent.state.messages = previous_messages
        if runtime.agentic_planning:
            summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
            _inject_planning(runtime.agent, summary)
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
                _inject_planning(runtime.agent, summary)
            if runtime.research_mode:
                _inject_research_prompt(runtime.agent)
            _persist(runtime)
            return jsonify({"ok": True, "session": name})

        if action == "load":
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
            result = _condense_session_context(runtime)
            _persist(runtime)
            return jsonify(result)

        return jsonify({"error": "unsupported action"}), 400

    return app


def main() -> None:
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
