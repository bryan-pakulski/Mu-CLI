from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, render_template, request

from mu_cli.core.types import Role
from mu_cli.webapp.contracts import (
    ContractValidationError,
    parse_pricing_request,
    parse_settings_update_request,
    parse_upload_delete_name,
    parse_uploads_request,
)
from mu_cli.webapp.services_uploads import UploadService, UploadServiceDeps


@dataclass(slots=True)
class StateRouteDeps:
    discover_git_repos: Callable[[Path], list[str]]
    is_git_repo: Callable[[Path], bool]
    git_current_branch: Callable[[Path], str | None]
    git_branches: Callable[[Path], list[str]]
    get_model_catalog: Callable[[dict[str, str | None]], dict[str, list[str]]]
    mutate_for_settings: Callable[[Any, dict[str, Any]], None]
    persist: Callable[[Any], None]
    remove_uploaded_entry: Callable[[Any, str], bool]
    clear_all_stored_data: Callable[[Any], dict[str, int]]
    telemetry_snapshot: Callable[[Any], dict[str, Any]]
    record_telemetry_action: Callable[[Any, str], None]


def register_state_routes(app, runtime: Any, deps: StateRouteDeps) -> None:
    upload_service = UploadService(UploadServiceDeps(persist=deps.persist, remove_uploaded_entry=deps.remove_uploaded_entry))

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
                "telemetry": deps.telemetry_snapshot(runtime),
            }
        )


    @app.post("/api/state/clear-all")
    def clear_all_state():
        deps.record_telemetry_action(runtime, "state_clear_all")
        stats = deps.clear_all_stored_data(runtime)
        return jsonify({"ok": True, "cleared": stats, "session": runtime.session_name})

    @app.get("/api/telemetry")
    def telemetry_state():
        return jsonify({"telemetry": deps.telemetry_snapshot(runtime)})

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
        try:
            req = parse_settings_update_request(request.get_json(force=True))
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        deps.mutate_for_settings(runtime, req.payload)
        deps.persist(runtime)
        return jsonify({"ok": True})

    @app.route("/api/pricing", methods=["GET", "POST"])
    def pricing_settings():
        if request.method == "GET":
            return jsonify({"pricing": runtime.pricing.data})

        try:
            req = parse_pricing_request(request.get_json(force=True))
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        payload = req.payload
        if "pricing" in payload:
            pricing_payload = payload.get("pricing")
            runtime.pricing.data = pricing_payload
            runtime.pricing.save()
            return jsonify({"ok": True, "pricing": runtime.pricing.data})

        provider = str(payload.get("provider", "")).strip()
        model = str(payload.get("model", "")).strip()
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
        try:
            files = parse_uploads_request(request.files.getlist("files"))
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400

        uploaded = upload_service.upload_files(runtime, files)
        return jsonify({"ok": True, "uploads": uploaded})

    @app.delete("/api/uploads")
    def clear_uploads():
        removed = upload_service.clear_uploads(runtime)
        return jsonify({"ok": True, "removed": removed})

    @app.delete("/api/uploads/<name>")
    def delete_upload(name: str):
        try:
            safe_name = parse_upload_delete_name(name)
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        ok, removed = upload_service.delete_upload(runtime, safe_name)
        if not ok:
            return jsonify({"error": "uploaded file not found"}), 404
        return jsonify({"ok": True, "removed": removed})

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
