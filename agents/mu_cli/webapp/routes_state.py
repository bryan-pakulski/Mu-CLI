from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, render_template, request
from werkzeug.utils import secure_filename

from mu_cli.core.types import Role


@dataclass(slots=True)
class StateRouteDeps:
    discover_git_repos: Callable[[Path], list[str]]
    is_git_repo: Callable[[Path], bool]
    git_current_branch: Callable[[Path], str | None]
    git_branches: Callable[[Path], list[str]]
    get_model_catalog: Callable[[dict[str, str | None]], dict[str, list[str]]]
    get_models: Callable[[str, str | None], list[str]]
    provider_api_key: Callable[[Any], str | None]
    refresh_tooling: Callable[[Any], None]
    new_agent: Callable[[Any], Any]
    inject_planning: Callable[[Any, str | None, str | None], None]
    inject_research_prompt: Callable[[Any], None]
    sync_skill_prompts: Callable[[Any], None]
    git_agent_instruction: Callable[[Any], str | None]
    persist: Callable[[Any], None]
    remove_uploaded_entry: Callable[[Any, str], bool]


def register_state_routes(app, runtime: Any, deps: StateRouteDeps) -> None:
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def state():
        sessions = runtime.session_store.list_sessions()
        git_repos: list[str] = []
        git_current_repo: str | None = None
        git_current_branch: str | None = None
        git_branches: list[str] = []
        if runtime.workspace_path:
            workspace = Path(runtime.workspace_path).expanduser()
            git_repos = deps.discover_git_repos(workspace)
            if deps.is_git_repo(workspace):
                git_current_repo = str(workspace)
            elif git_repos:
                git_current_repo = git_repos[0]
            if git_current_repo:
                repo_path = Path(git_current_repo)
                git_current_branch = deps.git_current_branch(repo_path)
                git_branches = deps.git_branches(repo_path)
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
                "models": deps.get_model_catalog({"openai": runtime.openai_api_key, "gemini": runtime.google_api_key}),
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
                "skills": runtime.skill_store.list_skills() if runtime.skill_store else [],
                "enabled_skills": runtime.enabled_skills,
                "workspace_index_stats": (
                    runtime.workspace_store.snapshot.index_stats if runtime.workspace_store.snapshot else {}
                ),
                "openai_api_key": runtime.openai_api_key,
                "google_api_key": runtime.google_api_key,
            }
        )

    @app.get("/api/skills/<name>")
    def skill_content(name: str):
        if runtime.skill_store is None:
            return jsonify({"error": "skills not configured"}), 404
        skill = runtime.skill_store.load_skill(name)
        if skill is None:
            return jsonify({"error": "skill not found"}), 404
        return jsonify({"name": skill.name, "content": skill.content})

    @app.post("/api/settings")
    def update_settings():
        payload = request.get_json(force=True)

        runtime.provider = str(payload.get("provider", runtime.provider))
        if "openai_api_key" in payload:
            runtime.openai_api_key = payload.get("openai_api_key") or None
        if "google_api_key" in payload:
            runtime.google_api_key = payload.get("google_api_key") or None
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

        deps.persist(runtime)
        return jsonify({"ok": True})

    @app.route("/api/pricing", methods=["GET", "POST"])
    def pricing_settings():
        if request.method == "GET":
            return jsonify({"pricing": runtime.pricing.data})

        payload = request.get_json(force=True)
        if "pricing" in payload:
            pricing_payload = payload.get("pricing")
            if not isinstance(pricing_payload, dict):
                return jsonify({"error": "pricing must be a JSON object"}), 400
            runtime.pricing.data = pricing_payload
            runtime.pricing.save()
            return jsonify({"ok": True, "pricing": runtime.pricing.data})

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

        return jsonify({"cwd": str(path), "parent": str(path.parent) if path.parent != path else None, "children": children})

    @app.get("/api/git/repos")
    def list_git_repos():
        raw_workspace = str(request.args.get("workspace", "") or "").strip()
        if not raw_workspace:
            return jsonify({"repos": []})
        workspace = Path(raw_workspace).expanduser()
        return jsonify({"repos": deps.discover_git_repos(workspace)})

    @app.get("/api/git/branches")
    def list_git_branches():
        raw_repo = str(request.args.get("repo", "") or "").strip()
        if not raw_repo:
            return jsonify({"error": "repo is required"}), 400
        repo = Path(raw_repo).expanduser()
        if not deps.is_git_repo(repo):
            return jsonify({"error": "repo is not a git repository"}), 400
        return jsonify({"repo": str(repo), "current_branch": deps.git_current_branch(repo), "branches": deps.git_branches(repo)})

    @app.post("/api/git/branch")
    def git_branch_action():
        payload = request.get_json(force=True)
        action = str(payload.get("action", "")).strip()
        raw_repo = str(payload.get("repo", "")).strip()
        if not raw_repo:
            return jsonify({"error": "repo is required"}), 400
        repo = Path(raw_repo).expanduser()
        if not deps.is_git_repo(repo):
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
        return jsonify({"ok": True, "repo": str(repo), "current_branch": deps.git_current_branch(repo), "branches": deps.git_branches(repo), "output": (proc.stdout or "").strip()})

    @app.get("/api/git/diff")
    def git_diff_status():
        raw_repo = str(request.args.get("repo", "") or "").strip()
        if not raw_repo:
            return jsonify({"error": "repo is required"}), 400
        repo = Path(raw_repo).expanduser()
        if not deps.is_git_repo(repo):
            return jsonify({"error": "repo is not a git repository"}), 400

        status_proc = subprocess.run(["git", "-C", str(repo), "status", "--short"], text=True, capture_output=True, check=False)
        diff_proc = subprocess.run(["git", "-C", str(repo), "diff"], text=True, capture_output=True, check=False)
        cached_diff_proc = subprocess.run(["git", "-C", str(repo), "diff", "--cached"], text=True, capture_output=True, check=False)
        if status_proc.returncode != 0 or diff_proc.returncode != 0 or cached_diff_proc.returncode != 0:
            return jsonify({"error": "unable to read git diff/status"}), 400

        return jsonify({"repo": str(repo), "status": (status_proc.stdout or "").strip(), "diff": (diff_proc.stdout or "").strip(), "cached_diff": (cached_diff_proc.stdout or "").strip()})

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

        deps.persist(runtime)
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
        deps.persist(runtime)
        return jsonify({"ok": True, "removed": removed})

    @app.delete("/api/uploads/<name>")
    def delete_upload(name: str):
        safe_name = Path(name).name
        session_dir = runtime.uploads_dir / runtime.session_name
        target = session_dir / safe_name
        if not target.exists() or not target.is_file():
            return jsonify({"error": "uploaded file not found"}), 404

        target.unlink()
        deps.remove_uploaded_entry(runtime, safe_name)
        deps.persist(runtime)
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
