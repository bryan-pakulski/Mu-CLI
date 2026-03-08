from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


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
    GetWorkspaceFileContextTool,
    GitTool,
    ListWorkspaceFilesTool,
    ReadFileTool,
    WriteFileTool,
)
from mu_cli.workspace import WorkspaceStore


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
    workspace_store: WorkspaceStore
    session_store: SessionStore
    pricing: PricingCatalog
    tools: list[Tool]
    agent: Agent
    traces: list[str]
    session_usage: dict[str, float]
    session_turns: list[dict]


def _build_provider(name: str, model: str, api_key: str | None):
    if name == "echo":
        return EchoProvider()
    if name == "openai":
        return OpenAIProvider(model=model, api_key=api_key)
    if name == "gemini":
        return GeminiProvider(model=model, api_key=api_key)
    raise ValueError(f"Unsupported provider: {name}")


def _planning_prompt(workspace_summary: str | None = None) -> str:
    if workspace_summary:
        return f"{PLANNING_PROMPT_BASE} Workspace context: {workspace_summary}"
    return PLANNING_PROMPT_BASE


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
    provider = _build_provider(runtime.provider, runtime.model, runtime.api_key)

    def on_approval(tool_name: str, args: dict) -> bool:
        mode = runtime.approval_mode
        if mode == "auto":
            return True
        if mode == "deny":
            return False
        runtime.traces.append(
            "approval: mode=ask is not interactive in GUI; mutating tool denied "
            f"name={tool_name} args={args}"
        )
        return False

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
            "timestamp": datetime.now(UTC).isoformat(),
            "session": runtime.session_name,
            "provider": report["provider"],
            "model": report["model"],
            "input_tokens": int(report["input_tokens"]),
            "output_tokens": int(report["output_tokens"]),
            "total_tokens": int(report["total_tokens"]),
            "estimated_cost_usd": float(report["estimated_cost_usd"]),
        }
    )


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
    runtime.agent.state.messages = loaded.messages
    runtime.session_usage = dict(loaded.usage_totals or {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0, "estimated_cost_usd": 0.0})
    runtime.session_turns = list(loaded.turns or [])

    if runtime.workspace_path:
        path = Path(runtime.workspace_path).expanduser()
        if path.exists() and path.is_dir():
            runtime.workspace_store.attach(path)

    if runtime.agentic_planning:
        summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
        _inject_planning(runtime.agent, summary)

    return True


def create_app():
    from flask import Flask, Response, jsonify, render_template, request, stream_with_context

    app = Flask(__name__, template_folder="templates")

    workspace_store = WorkspaceStore(Path(".mu_cli/workspaces"))
    tools: list[Tool] = [
        ReadFileTool(),
        WriteFileTool(),
        ApplyPatchTool(),
        GitTool(),
        ListWorkspaceFilesTool(workspace_store),
        GetWorkspaceFileContextTool(workspace_store),
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
        workspace_store=workspace_store,
        session_store=session_store,
        pricing=PricingCatalog(Path(".mu_cli/pricing.json")),
        tools=tools,
        agent=Agent(provider=EchoProvider(), tools=tools),
        traces=[],
        session_usage={"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0, "estimated_cost_usd": 0.0},
        session_turns=[],
    )
    runtime.agent = _new_agent(runtime)
    runtime.agent.add_system_prompt(runtime.system_prompt)
    _inject_planning(runtime.agent)

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
                "session": runtime.session_name,
                "workspace": runtime.workspace_path,
                "debug": runtime.debug,
                "agentic_planning": runtime.agentic_planning,
                "models": MODELS_BY_PROVIDER,
                "sessions": sessions,
                "messages": [asdict(m) for m in runtime.agent.state.messages if m.role is not Role.SYSTEM],
                "traces": runtime.traces[-50:],
                "session_usage": runtime.session_usage,
                "session_turns": runtime.session_turns[-200:],
                "pricing": runtime.pricing.data,
            }
        )

    @app.post("/api/chat")
    def chat():
        payload = request.get_json(force=True)
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required"}), 400

        reply = runtime.agent.step(text)
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
                reply = runtime.agent.step(text)
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
        runtime.api_key = payload.get("api_key", runtime.api_key)
        runtime.approval_mode = str(payload.get("approval_mode", runtime.approval_mode))
        runtime.debug = bool(payload.get("debug", runtime.debug))
        runtime.agentic_planning = bool(payload.get("agentic_planning", runtime.agentic_planning))

        workspace = payload.get("workspace")
        if workspace:
            path = Path(str(workspace)).expanduser()
            if path.exists() and path.is_dir():
                snapshot = runtime.workspace_store.attach(path)
                runtime.workspace_path = str(path)
                runtime.traces.append(f"workspace-attached: {snapshot.root} files={len(snapshot.files)}")

        previous_messages = list(runtime.agent.state.messages)
        runtime.agent = _new_agent(runtime)
        runtime.agent.state.messages = previous_messages
        if runtime.agentic_planning:
            summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
            _inject_planning(runtime.agent, summary)

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
            runtime.session_usage = {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0, "estimated_cost_usd": 0.0}
            runtime.session_turns = []
            if runtime.agentic_planning:
                summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
                _inject_planning(runtime.agent, summary)
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

        return jsonify({"error": "unsupported action"}), 400

    return app


def main() -> None:
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
