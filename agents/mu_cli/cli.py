#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from mu_cli.agent import Agent
from mu_cli.core.types import Message, Role, ToolCall, UsageStats
from mu_cli.models import MODELS_BY_PROVIDER, get_models
from mu_cli.policy import ApprovalPolicy
from mu_cli.pricing import PricingCatalog, estimate_tokens
from mu_cli.providers.echo import EchoProvider
from mu_cli.providers.gemini import GeminiProvider
from mu_cli.providers.openai import OpenAIProvider
from mu_cli.session import SessionState, SessionStore
from mu_cli.tools.base import Tool
from mu_cli.tools.filesystem import (
    ApplyPatchTool,
    ExtractLinksContextTool,
    FetchPdfContextTool,
    GetWorkspaceFileContextTool,
    GitTool,
    FetchUrlContextTool,
    SearchArxivPapersTool,
    SearchWebContextTool,
    ListWorkspaceFilesTool,
    ReadFileTool,
    WriteFileTool,
)
from mu_cli.workspace import WorkspaceStore

PLANNING_PROMPT_BASE = (
    "You are operating in human-in-the-loop developer mode. "
    "Before significant actions, provide a short plan and rationale. "
    "Prefer smallest safe changes and explain what tool(s) you need. "
    "For workspace tasks: first discover with list_workspace_files, then read only specific files with "
    "get_workspace_file_context. Do not request the whole codebase unless explicitly asked. "
    "When modifying existing files, prefer apply_patch for targeted edits; use write_file for new files or full rewrites only when explicitly requested. "
    "Before and after mutating edits, use git diff (or equivalent) to verify minimal changes. "
    "For any request involving repository state, files, diffs, or edits, tool usage is required before final claims. "
    "For mutating actions, clearly state intended edits before executing."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provider-agnostic AI CLI (human-in-the-loop)")
    parser.add_argument("--provider", default="echo", choices=["echo", "openai", "gemini"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--system", default="You are a helpful coding assistant. Keep responses concise.")
    parser.add_argument("--workspace", default=None, help="Optional workspace path to pre-attach")
    parser.add_argument("--pricing-config", default=".mu_cli/pricing.json")
    parser.add_argument("--session", default="default", help="Session name used for persistence")
    parser.add_argument("--no-resume", action="store_true", help="Do not resume persisted session state")
    parser.add_argument("--approval-mode", choices=["ask", "auto", "deny"], default="ask")
    parser.add_argument("--list-models", action="store_true", help="Print supported model catalog and exit")
    parser.add_argument("--no-agentic-planning", action="store_true")
    parser.add_argument("--debug", action="store_true", help="Print debug traces of model/tool activity")
    return parser


def _build_provider(name: str, model: str | None, api_key: str | None):
    if name == "echo":
        return EchoProvider()
    if name == "openai":
        return OpenAIProvider(model=model or "gpt-4o-mini", api_key=api_key)
    if name == "gemini":
        return GeminiProvider(model=model or "gemini-2.0-flash", api_key=api_key)
    raise ValueError(f"Unsupported provider: {name}")


def _format_models(provider: str | None = None) -> str:
    if provider:
        models = get_models(provider)
        return f"{provider}:\n" + "\n".join(f"- {model}" for model in models)
    chunks = []
    for key, values in MODELS_BY_PROVIDER.items():
        chunks.append(f"{key}:\n" + "\n".join(f"- {item}" for item in values))
    return "\n\n".join(chunks)


def _format_tool_tip(tool: Tool) -> str:
    required = set(tool.schema.get("required", []))
    properties: dict[str, dict[str, str]] = tool.schema.get("properties", {})
    arg_parts = []
    for name, prop in properties.items():
        suffix = " (required)" if name in required else ""
        description = prop.get("description", "")
        arg_parts.append(f"- {name}{suffix}: {description}".rstrip())
    args_block = "\n".join(arg_parts) if arg_parts else "- (no args)"
    mut = " [mutating]" if getattr(tool, "mutating", False) else ""
    return f"{tool.name}{mut}: {tool.description}\n{args_block}"


def build_help_text(tools: Sequence[Tool]) -> str:
    tool_lines = "\n\n".join(_format_tool_tip(tool) for tool in tools)
    return (
        "Commands:\n"
        "- /help: Show this help text.\n"
        "- /tools: List available tools and their arguments.\n"
        "- /tool-help <name>: Show detailed help for one tool.\n"
        "- /workspace attach <path>: Index a workspace for context-aware tooling.\n"
        "- /workspace status: Show attached workspace summary.\n"
        "- /models [provider]: Show available model list.\n"
        "- /model select <name>: Switch active model for current provider.\n"
        "- /approvals status|set <ask|auto|deny>: Manage mutating-tool approval mode.\n"
        "- /agentic status: Show planning-prompt injection status.\n"
        "- /debug status|on|off: Debug tracing mode.\n"
        "- /session status|list|new <name>|load <name>|delete <name>: Session management.\n"
        "- /quit (or /q, exit): Exit the CLI.\n\n"
        f"Tools:\n{tool_lines}"
    )


def _build_planning_prompt(workspace_summary: str | None = None) -> str:
    if not workspace_summary:
        return PLANNING_PROMPT_BASE
    return f"{PLANNING_PROMPT_BASE} Workspace context: {workspace_summary}"


def _has_planning_prompt(agent: Agent) -> bool:
    return any(
        message.role is Role.SYSTEM and message.metadata.get("kind") == "agentic_planning"
        for message in agent.state.messages
    )


def _inject_planning_prompt(agent: Agent, workspace_summary: str | None = None) -> None:
    if _has_planning_prompt(agent):
        return
    agent.state.messages.append(
        Message(
            role=Role.SYSTEM,
            content=_build_planning_prompt(workspace_summary),
            metadata={"kind": "agentic_planning"},
        )
    )


class RuntimeContext:
    def __init__(
        self,
        provider_name: str,
        model_name: str,
        api_key: str | None,
        workspace_store: WorkspaceStore,
        tools: list[Tool],
        approval_policy: ApprovalPolicy,
        pricing: PricingCatalog,
        session_store: SessionStore,
        workspace_path: str | None,
        agentic_planning_enabled: bool,
        system_prompt: str,
        debug_enabled: bool,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_key = api_key
        self.workspace_store = workspace_store
        self.tools = tools
        self.approval_policy = approval_policy
        self.pricing = pricing
        self.session_store = session_store
        self.workspace_path = workspace_path
        self.agentic_planning_enabled = agentic_planning_enabled
        self.system_prompt = system_prompt
        self.debug_enabled = debug_enabled


class CommandCompleter:
    def __init__(self, tools: Sequence[Tool]) -> None:
        self.tool_names = sorted(tool.name for tool in tools)
        self.commands = [
            "/help",
            "/tools",
            "/tool-help",
            "/workspace",
            "/models",
            "/model",
            "/approvals",
            "/agentic",
            "/debug",
            "/session",
            "/quit",
            "/q",
            "exit",
        ]

    def matches(self, text: str, line_buffer: str) -> list[str]:
        stripped = line_buffer.lstrip()
        if stripped.startswith("/tool-help "):
            return [name for name in self.tool_names if name.startswith(text)]
        if stripped.startswith("/tool "):
            return [name for name in self.tool_names if name.startswith(text)]
        if stripped.startswith("/workspace "):
            return [item for item in ["attach", "status"] if item.startswith(text)]
        if stripped.startswith("/approvals "):
            return [item for item in ["status", "set"] if item.startswith(text)]
        if stripped.startswith("/model "):
            return [item for item in ["select"] if item.startswith(text)]
        if stripped.startswith("/agentic "):
            return [item for item in ["status"] if item.startswith(text)]
        if stripped.startswith("/debug "):
            return [item for item in ["status", "on", "off"] if item.startswith(text)]
        if stripped.startswith("/session "):
            return [item for item in ["status", "list", "new", "load", "delete"] if item.startswith(text)]
        return [command for command in self.commands if command.startswith(text)]

    def complete(self, text: str, state: int) -> str | None:
        try:
            import readline
        except ImportError:
            return None

        options = self.matches(text, readline.get_line_buffer())
        if state < len(options):
            return options[state]
        return None


def setup_autocomplete(tools: Sequence[Tool]) -> None:
    try:
        import readline
    except ImportError:
        return
    completer = CommandCompleter(tools)
    readline.parse_and_bind("tab: complete")
    readline.set_completer(completer.complete)


def _debug_model_response(context: RuntimeContext, message: Message, tool_calls: list[ToolCall]) -> None:
    if not context.debug_enabled:
        return
    print("\n[debug] model response")
    print(f"[debug] assistant_content={message.content!r}")
    if tool_calls:
        print("[debug] tool_requests:")
        for call in tool_calls:
            print(f"  - id={call.call_id} name={call.name} args={call.args}")


def _debug_tool_run(context: RuntimeContext, name: str, args: dict, ok: bool, output: str) -> None:
    if not context.debug_enabled:
        return
    print("\n[debug] tool execution")
    print(f"[debug] name={name} ok={ok} args={args}")
    print(f"[debug] output_preview={output[:300]}")


def _make_agent(context: RuntimeContext) -> Agent:
    provider = _build_provider(context.provider_name, context.model_name, context.api_key)
    return Agent(
        provider=provider,
        tools=context.tools,
        on_approval=context.approval_policy.should_approve,
        on_model_response=lambda message, calls: _debug_model_response(context, message, calls),
        on_tool_run=lambda name, args, ok, output: (
            context.workspace_store.record_tool_run(name, args, output, ok),
            _debug_tool_run(context, name, args, ok, output),
        ),
        strict_tool_usage=True,
    )


def _initialize_fresh_agent(context: RuntimeContext, agent: Agent) -> None:
    agent.state.messages = []
    agent.add_system_prompt(context.system_prompt)
    if context.workspace_path:
        path = Path(context.workspace_path).expanduser()
        if path.exists() and path.is_dir():
            snapshot = context.workspace_store.attach(path)
            agent.add_system_prompt(
                "Workspace attached. Use list_workspace_files to discover relevant files, "
                "then use get_workspace_file_context for specific files only. "
                f"Indexed files: {len(snapshot.files)}."
            )
    if context.agentic_planning_enabled:
        workspace_summary = context.workspace_store.summary() if context.workspace_store.snapshot else None
        _inject_planning_prompt(agent, workspace_summary)


def _handle_session_command(rest: str, context: RuntimeContext, agent: Agent) -> tuple[str, Agent | None]:
    rest = rest.strip()
    if rest == "status":
        return f"Active session: {context.session_store.session_name}", None
    if rest == "list":
        sessions = context.session_store.list_sessions()
        return "Sessions:\n" + ("\n".join(f"- {name}" for name in sessions) if sessions else "(none)"), None
    if rest.startswith("new "):
        name = rest[len("new ") :].strip()
        if not name:
            return "Usage: /session new <name>", None
        context.session_store.use(name)
        new_agent = _make_agent(context)
        _initialize_fresh_agent(context, new_agent)
        return f"Started new session: {name}", new_agent
    if rest.startswith("load "):
        name = rest[len("load ") :].strip()
        if not name:
            return "Usage: /session load <name>", None
        context.session_store.use(name)
        loaded = context.session_store.load()
        if loaded is None:
            return f"Session not found: {name}", None
        context.provider_name = loaded.provider
        context.model_name = loaded.model
        context.workspace_path = loaded.workspace
        context.approval_policy.mode = loaded.approval_mode
        new_agent = _make_agent(context)
        new_agent.state.messages = loaded.messages
        if context.workspace_path:
            path = Path(context.workspace_path).expanduser()
            if path.exists() and path.is_dir():
                context.workspace_store.attach(path)
        if context.agentic_planning_enabled:
            _inject_planning_prompt(new_agent, context.workspace_store.summary() if context.workspace_store.snapshot else None)
        return f"Loaded session: {name}", new_agent
    if rest.startswith("delete "):
        name = rest[len("delete ") :].strip()
        if not name:
            return "Usage: /session delete <name>", None
        if name == context.session_store.session_name:
            return "Cannot delete active session.", None
        deleted = context.session_store.delete(name)
        return (f"Deleted session: {name}" if deleted else f"Session not found: {name}"), None

    return "Usage: /session status|list|new <name>|load <name>|delete <name>", None


def _handle_local_command(user_input: str, context: RuntimeContext, agent: Agent) -> tuple[bool, str | None, Agent | None]:
    if user_input in {"/help", "/tools"}:
        return True, build_help_text(context.tools), None

    if user_input.startswith("/tool-help"):
        _, _, raw_name = user_input.partition(" ")
        name = raw_name.strip()
        if not name:
            return True, "Usage: /tool-help <name>", None
        tool = next((item for item in context.tools if item.name == name), None)
        if tool is None:
            return True, f"Unknown tool: {name}", None
        return True, _format_tool_tip(tool), None

    if user_input.startswith("/workspace "):
        _, _, rest = user_input.partition(" ")
        if rest.startswith("attach "):
            path = Path(rest[len("attach ") :].strip()).expanduser()
            if not path.exists() or not path.is_dir():
                return True, f"Workspace path not found: {path}", None
            snapshot = context.workspace_store.attach(path)
            context.workspace_path = str(path)
            if context.agentic_planning_enabled:
                _inject_planning_prompt(
                    agent,
                    workspace_summary=f"root={snapshot.root}, indexed_files={len(snapshot.files)}",
                )
            return True, f"Attached workspace: {snapshot.root} (indexed files: {len(snapshot.files)})", None
        if rest.strip() == "status":
            return True, context.workspace_store.summary(), None
        return True, "Usage: /workspace attach <path> | /workspace status", None

    if user_input.startswith("/models"):
        _, _, provider = user_input.partition(" ")
        provider = provider.strip() or None
        return True, _format_models(provider), None

    if user_input.startswith("/model "):
        _, _, rest = user_input.partition(" ")
        if rest.startswith("select "):
            selected = rest[len("select ") :].strip()
            if selected not in get_models(context.provider_name):
                return True, f"Unsupported model `{selected}` for provider `{context.provider_name}`", None
            context.model_name = selected
            new_agent = _make_agent(context)
            new_agent.state.messages = list(agent.state.messages)
            return True, f"Switched model to {selected}", new_agent
        return True, "Usage: /model select <name>", None

    if user_input.startswith("/approvals "):
        _, _, rest = user_input.partition(" ")
        if rest.strip() == "status":
            return True, f"Approval mode: {context.approval_policy.mode}", None
        if rest.startswith("set "):
            mode = rest[len("set ") :].strip()
            if mode not in {"ask", "auto", "deny"}:
                return True, "Usage: /approvals set <ask|auto|deny>", None
            context.approval_policy.mode = mode
            return True, f"Approval mode set to: {mode}", None
        return True, "Usage: /approvals status | /approvals set <ask|auto|deny>", None

    if user_input.startswith("/agentic"):
        return (
            True,
            f"Agentic planning prompt: {'enabled' if context.agentic_planning_enabled else 'disabled'}",
            None,
        )

    if user_input.startswith("/debug"):
        _, _, rest = user_input.partition(" ")
        mode = rest.strip()
        if mode in {"", "status"}:
            return True, f"Debug mode: {'on' if context.debug_enabled else 'off'}", None
        if mode == "on":
            context.debug_enabled = True
            return True, "Debug mode enabled", None
        if mode == "off":
            context.debug_enabled = False
            return True, "Debug mode disabled", None
        return True, "Usage: /debug status|on|off", None

    if user_input.startswith("/session "):
        _, _, rest = user_input.partition(" ")
        output, replacement = _handle_session_command(rest, context, agent)
        return True, output, replacement

    return False, None, None


def _print_turn_report(
    agent: Agent,
    provider_name: str,
    model_name: str,
    pricing: PricingCatalog,
    user_input: str,
    assistant_output: str,
) -> None:
    usage = agent.last_usage or UsageStats(
        input_tokens=estimate_tokens(user_input),
        output_tokens=estimate_tokens(assistant_output),
        total_tokens=estimate_tokens(user_input) + estimate_tokens(assistant_output),
    )
    report = pricing.estimate_cost(provider_name, model_name, usage)
    print(
        "\n[turn-report] "
        f"provider={report.provider} model={report.model} "
        f"in={report.usage.input_tokens} out={report.usage.output_tokens} total={report.usage.total_tokens} "
        f"est_cost=${report.estimated_cost_usd:.6f}"
    )


def _persist_session(context: RuntimeContext, agent: Agent) -> None:
    state = SessionState(
        provider=context.provider_name,
        model=context.model_name,
        workspace=context.workspace_path,
        approval_mode=context.approval_policy.mode,
        messages=agent.state.messages,
    )
    context.session_store.save(state)


def run() -> int:
    args = build_parser().parse_args()

    if args.list_models:
        print(_format_models())
        return 0

    workspace_store = WorkspaceStore(Path(".mu_cli/workspaces"))
    tools: list[Tool] = [
        ReadFileTool(lambda: Path(workspace_store.snapshot.root) if workspace_store.snapshot else None),
        WriteFileTool(lambda: Path(workspace_store.snapshot.root) if workspace_store.snapshot else None),
        ApplyPatchTool(lambda: Path(workspace_store.snapshot.root) if workspace_store.snapshot else None),
        GitTool(lambda: Path(workspace_store.snapshot.root) if workspace_store.snapshot else None),
        FetchUrlContextTool(),
        FetchPdfContextTool(),
        ExtractLinksContextTool(),
        SearchWebContextTool(),
        SearchArxivPapersTool(),
        ListWorkspaceFilesTool(workspace_store),
        GetWorkspaceFileContextTool(workspace_store),
    ]
    setup_autocomplete(tools)

    session_store = SessionStore(Path(".mu_cli/sessions"), args.session)
    resumed = None if args.no_resume else session_store.load()

    provider_name = resumed.provider if resumed else args.provider
    model_name = resumed.model if resumed else (args.model or get_models(args.provider)[0])
    workspace_path = resumed.workspace if resumed else args.workspace
    approval_mode = resumed.approval_mode if resumed else args.approval_mode

    context = RuntimeContext(
        provider_name=provider_name,
        model_name=model_name,
        api_key=args.api_key,
        workspace_store=workspace_store,
        tools=tools,
        approval_policy=ApprovalPolicy(mode=approval_mode),
        pricing=PricingCatalog(Path(args.pricing_config)),
        session_store=session_store,
        workspace_path=workspace_path,
        agentic_planning_enabled=not args.no_agentic_planning,
        system_prompt=args.system,
        debug_enabled=args.debug,
    )

    agent = _make_agent(context)
    if resumed and resumed.messages:
        agent.state.messages = resumed.messages
    else:
        agent.add_system_prompt(args.system)

    if context.workspace_path:
        path = Path(context.workspace_path).expanduser()
        if path.exists() and path.is_dir():
            snapshot = context.workspace_store.attach(path)
            if not resumed:
                agent.add_system_prompt(
                    "Workspace attached. Use list_workspace_files to discover relevant files, "
                    "then use get_workspace_file_context for specific files only. "
                    f"Indexed files: {len(snapshot.files)}."
                )

    if context.agentic_planning_enabled:
        workspace_summary = context.workspace_store.summary() if context.workspace_store.snapshot else None
        _inject_planning_prompt(agent, workspace_summary)

    print(f"ai-cli [{context.provider_name}:{context.model_name}] started. Type /quit to exit.")
    print("Tip: use /help for commands and tool descriptions.")

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            _persist_session(context, agent)
            return 0

        if not user_input:
            continue
        if user_input in {"/quit", "/q", "exit"}:
            _persist_session(context, agent)
            print("Goodbye.")
            return 0

        handled, output, replacement_agent = _handle_local_command(user_input, context, agent)
        if replacement_agent is not None:
            agent = replacement_agent
        if handled:
            print(f"\n{output}")
            _persist_session(context, agent)
            continue

        message = agent.step(user_input)
        print(f"\nassistant> {message.content}")
        _print_turn_report(agent, context.provider_name, context.model_name, context.pricing, user_input, message.content)
        _persist_session(context, agent)


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
