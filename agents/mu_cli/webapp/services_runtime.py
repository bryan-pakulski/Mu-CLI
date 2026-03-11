from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class RuntimeMutationDeps:
    get_models: Callable[[str, str | None], list[str]]
    provider_api_key: Callable[[Any], str | None]
    attach_workspace_if_available: Callable[[Any], None]
    initialize_fresh_session_state: Callable[[Any], None]
    initialize_fresh_session_state_reset_summary: Callable[[Any], None]
    refresh_tooling: Callable[[Any], None]
    new_agent: Callable[[Any], Any]
    inject_planning: Callable[[Any, str | None, str | None], None]
    inject_research_prompt: Callable[[Any], None]
    sync_skill_prompts: Callable[[Any], None]
    git_agent_instruction: Callable[[Any], str | None]


def mutate_runtime_for_new_session(runtime: Any, payload: dict[str, Any], name: str, deps: RuntimeMutationDeps) -> None:
    runtime.provider = str(payload.get("provider", runtime.provider))
    selected_model = str(payload.get("model", runtime.model))
    if "openai_api_key" in payload:
        runtime.openai_api_key = payload.get("openai_api_key") or None
    if "google_api_key" in payload:
        runtime.google_api_key = payload.get("google_api_key") or None
    if "ollama_endpoint" in payload:
        runtime.ollama_endpoint = payload.get("ollama_endpoint") or None
    available = deps.get_models(runtime.provider, deps.provider_api_key(runtime))
    runtime.model = selected_model if selected_model in available else (available[0] if available else runtime.model)
    runtime.agentic_planning = bool(payload.get("agentic_planning", runtime.agentic_planning))
    runtime.research_mode = bool(payload.get("research_mode", runtime.research_mode))
    runtime.approval_mode = str(payload.get("approval_mode", runtime.approval_mode))
    runtime.max_runtime_seconds = int(payload.get("max_runtime_seconds", runtime.max_runtime_seconds) or runtime.max_runtime_seconds)
    runtime.condense_enabled = bool(payload.get("condense_enabled", runtime.condense_enabled))
    runtime.condense_window = int(payload.get("condense_window", runtime.condense_window) or runtime.condense_window)

    enabled_skills = payload.get("enabled_skills")
    if isinstance(enabled_skills, list):
        runtime.enabled_skills = [str(item).strip() for item in enabled_skills if str(item).strip()]
    else:
        runtime.enabled_skills = []

    workspace = payload.get("workspace")
    runtime.workspace_path = str(workspace).strip() if workspace else None
    runtime.workspace_store.snapshot = None
    deps.attach_workspace_if_available(runtime)

    runtime.session_name = name
    runtime.session_store.use(name)
    deps.initialize_fresh_session_state(runtime)


def mutate_runtime_for_clear(runtime: Any, *, reset_summary_index: bool, deps: RuntimeMutationDeps) -> None:
    deps.attach_workspace_if_available(runtime)
    if reset_summary_index:
        deps.initialize_fresh_session_state_reset_summary(runtime)
    else:
        deps.initialize_fresh_session_state(runtime)


def mutate_runtime_for_settings(runtime: Any, payload: dict[str, Any], deps: RuntimeMutationDeps) -> None:
    runtime.provider = str(payload.get("provider", runtime.provider))
    if "openai_api_key" in payload:
        runtime.openai_api_key = payload.get("openai_api_key") or None
    if "google_api_key" in payload:
        runtime.google_api_key = payload.get("google_api_key") or None
    if "ollama_endpoint" in payload:
        runtime.ollama_endpoint = payload.get("ollama_endpoint") or None
    selected_model = str(payload.get("model", runtime.model))
    available = deps.get_models(runtime.provider, deps.provider_api_key(runtime))
    runtime.model = selected_model if selected_model in available else (available[0] if available else runtime.model)
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

    enabled_skills = payload.get("enabled_skills")
    if isinstance(enabled_skills, list):
        runtime.enabled_skills = [str(item).strip() for item in enabled_skills if str(item).strip()]

    workspace = payload.get("workspace")
    if workspace:
        path = Path(str(workspace)).expanduser()
        if path.exists() and path.is_dir():
            snapshot = runtime.workspace_store.attach(path)
            runtime.workspace_path = str(path)
            runtime.traces.append(f"workspace-attached: {snapshot.root} files={len(snapshot.files)}")

    previous_messages = list(runtime.agent.state.messages)
    deps.refresh_tooling(runtime)
    runtime.agent = deps.new_agent(runtime)
    runtime.agent.state.messages = previous_messages
    if runtime.agentic_planning:
        summary = runtime.workspace_store.summary() if runtime.workspace_store.snapshot else None
        deps.inject_planning(runtime.agent, summary, deps.git_agent_instruction(runtime))
    if runtime.research_mode:
        deps.inject_research_prompt(runtime.agent)
    deps.sync_skill_prompts(runtime)
