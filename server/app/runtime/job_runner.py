import asyncio
import copy
import json
import re
import subprocess
import urllib.parse
import urllib.request
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
from server.app.tools.registry import tool_registry

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
    "stage_protocol:",
    "stage_success_criteria:",
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




def _extract_requested_tool_name(output: str) -> str | None:
    calls = _extract_tool_calls(output)
    if not calls:
        return None
    return str(calls[0].get("tool_name") or "") or None


def _extract_tool_calls(output: str) -> list[dict]:
    text = output or ""
    calls: list[dict] = []

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
        if tool_name:
            calls.append({"tool_name": tool_name, "constraints": params})

    inline_match = re.search(r"constraints\.tool_name\s*=\s*([a-zA-Z0-9_-]+)", text)
    if inline_match:
        tool_name = inline_match.group(1)
        if tool_name and not any(item.get("tool_name") == tool_name for item in calls):
            calls.append({"tool_name": tool_name, "constraints": {}})

    return calls


def _safe_workspace_path(workspace_path: str | None) -> Path:
    base = Path(workspace_path or ".").expanduser().resolve()
    return base if base.exists() else Path(".").resolve()


def _safe_workspace_target(workspace: Path, file_path: str) -> Path | None:
    rel = str(file_path or "").strip()
    if not rel:
        return None
    target = (workspace / rel).resolve()
    if workspace not in target.parents and target != workspace:
        return None
    return target


def _safe_upload_store(workspace: Path) -> Path:
    store = (workspace / ".mu" / "uploaded_context").resolve()
    store.mkdir(parents=True, exist_ok=True)
    return store


def _text_from_html(html: str) -> str:
    cleaned = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _fetch_url(url: str, timeout_s: int = 20) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mu-CLI/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as response:  # noqa: S310
        payload = response.read()
        ctype = str(response.headers.get("content-type") or "")
    return ctype, payload.decode("utf-8", errors="replace")


def _run_builtin_tool(tool_name: str, workspace: Path, constraints: dict, session: SessionModel, job: JobModel) -> dict:
    if tool_name == "list_workspace_files":
        files = [str(path.relative_to(workspace)) for path in workspace.rglob("*") if path.is_file()][:200]
        return {"tool_name": tool_name, "workspace": str(workspace), "files": files}

    if tool_name == "read_file":
        rel = str(constraints.get("file_path") or "")
        target = _safe_workspace_target(workspace, rel)
        if not target:
            return {"tool_name": tool_name, "error": "file_path missing or outside workspace"}
        if not target.exists() or not target.is_file():
            return {"tool_name": tool_name, "error": "file does not exist"}
        return {
            "tool_name": tool_name,
            "file_path": rel,
            "content": target.read_text(encoding="utf-8", errors="replace")[:12000],
        }

    if tool_name == "write_file":
        rel = str(constraints.get("file_path") or "")
        content = str(constraints.get("content") or "")
        target = _safe_workspace_target(workspace, rel)
        if not target:
            return {"tool_name": tool_name, "error": "file_path missing or outside workspace"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "tool_name": tool_name,
            "file_path": rel,
            "bytes_written": len(content.encode("utf-8")),
        }

    if tool_name == "get_workspace_file_context":
        rel = str(constraints.get("file_path") or "")
        target = _safe_workspace_target(workspace, rel)
        if not target:
            return {"tool_name": tool_name, "error": "file_path missing or outside workspace"}
        if not target.exists() or not target.is_file():
            return {"tool_name": tool_name, "error": "file does not exist"}
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"tool_name": tool_name, "file_path": rel, "snippet": text[:2000]}

    if tool_name == "execute_command":
        command = str(constraints.get("command") or "").strip()
        if not command:
            return {"tool_name": tool_name, "error": "command is required"}
        timeout_s = min(60, max(1, int(constraints.get("timeout_s") or 15)))
        completed = subprocess.run(
            command,
            cwd=str(workspace),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "tool_name": tool_name,
            "command": command,
            "exit_code": completed.returncode,
            "stdout": (completed.stdout or "")[:12000],
            "stderr": (completed.stderr or "")[:8000],
        }

    if tool_name == "git":
        command = str(constraints.get("command") or "status --short")
        allowed = ["status", "log", "diff", "show", "branch", "rev-parse"]
        if not any(command.strip().startswith(item) for item in allowed):
            return {"tool_name": tool_name, "error": "only read-only git commands are allowed"}
        completed = subprocess.run(
            ["git", *command.split()],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {
            "tool_name": tool_name,
            "command": f"git {command}",
            "exit_code": completed.returncode,
            "stdout": (completed.stdout or "")[:12000],
            "stderr": (completed.stderr or "")[:8000],
        }

    if tool_name == "apply_patch":
        patch_text = str(constraints.get("patch") or "")
        if not patch_text:
            return {"tool_name": tool_name, "error": "patch is required"}
        target_file = workspace / ".mu" / "pending.patch"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(patch_text, encoding="utf-8")
        completed = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(target_file)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {
            "tool_name": tool_name,
            "exit_code": completed.returncode,
            "stdout": (completed.stdout or "")[:12000],
            "stderr": (completed.stderr or "")[:8000],
        }

    if tool_name == "fetch_url_context":
        url = str(constraints.get("url") or "").strip()
        if not url:
            return {"tool_name": tool_name, "error": "url is required"}
        try:
            ctype, text = _fetch_url(url)
            return {
                "tool_name": tool_name,
                "url": url,
                "content_type": ctype,
                "content": _text_from_html(text)[:6000],
            }
        except Exception as exc:  # noqa: BLE001
            return {"tool_name": tool_name, "url": url, "error": str(exc)}

    if tool_name == "extract_links_context":
        url = str(constraints.get("url") or "").strip()
        if not url:
            return {"tool_name": tool_name, "error": "url is required"}
        try:
            _, text = _fetch_url(url)
            links = re.findall(r'href=["\']([^"\']+)["\']', text, flags=re.IGNORECASE)
            normalized = []
            for link in links:
                normalized.append(urllib.parse.urljoin(url, link))
            unique = []
            seen = set()
            for link in normalized:
                if link in seen:
                    continue
                seen.add(link)
                unique.append(link)
            return {"tool_name": tool_name, "url": url, "links": unique[:100]}
        except Exception as exc:  # noqa: BLE001
            return {"tool_name": tool_name, "url": url, "error": str(exc)}

    if tool_name == "fetch_pdf_context":
        url = str(constraints.get("url") or "").strip()
        if not url:
            return {"tool_name": tool_name, "error": "url is required"}
        try:
            _, payload = _fetch_url(url)
            text = re.sub(r"\s+", " ", payload)
            return {"tool_name": tool_name, "url": url, "content": text[:6000]}
        except Exception as exc:  # noqa: BLE001
            return {"tool_name": tool_name, "url": url, "error": str(exc)}

    if tool_name == "search_web_context":
        query = str(constraints.get("query") or "").strip()
        if not query:
            return {"tool_name": tool_name, "error": "query is required"}
        search_url = f"https://duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        try:
            _, html = _fetch_url(search_url)
            titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, flags=re.IGNORECASE)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</div>', html, flags=re.IGNORECASE)
            parsed = []
            for idx, title in enumerate(titles[:5]):
                snip = ""
                if idx < len(snippets):
                    snip = (snippets[idx][0] or snippets[idx][1] or "")
                parsed.append({"title": _text_from_html(title), "snippet": _text_from_html(snip)})
            return {"tool_name": tool_name, "query": query, "results": parsed}
        except Exception as exc:  # noqa: BLE001
            return {"tool_name": tool_name, "query": query, "error": str(exc)}

    if tool_name == "search_arxiv_papers":
        query = str(constraints.get("query") or "").strip()
        if not query:
            return {"tool_name": tool_name, "error": "query is required"}
        max_results = min(10, max(1, int(constraints.get("max_results") or 5)))
        endpoint = (
            "http://export.arxiv.org/api/query?search_query="
            + urllib.parse.quote_plus(query)
            + f"&start=0&max_results={max_results}"
        )
        try:
            _, xml_text = _fetch_url(endpoint)
            root = ET.fromstring(xml_text)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            items = []
            for entry in root.findall("a:entry", ns):
                items.append(
                    {
                        "id": (entry.findtext("a:id", default="", namespaces=ns) or "").strip(),
                        "title": (entry.findtext("a:title", default="", namespaces=ns) or "").strip(),
                        "summary": (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()[:1200],
                    }
                )
            return {"tool_name": tool_name, "query": query, "results": items}
        except Exception as exc:  # noqa: BLE001
            return {"tool_name": tool_name, "query": query, "error": str(exc)}

    if tool_name == "score_sources":
        sources = constraints.get("sources") or []
        if not isinstance(sources, list):
            return {"tool_name": tool_name, "error": "sources must be a list"}
        scored = []
        for source in sources:
            if isinstance(source, dict):
                title = str(source.get("title") or "")
                summary = str(source.get("summary") or source.get("snippet") or "")
            else:
                title = str(source)
                summary = str(source)
            score = min(100, max(1, len(summary) // 40 + (20 if "arxiv" in title.lower() else 0)))
            scored.append({"title": title, "score": score, "summary": summary[:300]})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return {"tool_name": tool_name, "results": scored[:20]}

    if tool_name == "run_make_agent_job":
        nested_goal = str(constraints.get("goal") or "").strip()
        if not nested_goal:
            return {"tool_name": tool_name, "error": "goal is required"}
        return {
            "tool_name": tool_name,
            "status": "queued",
            "session_id": session.id,
            "goal": nested_goal,
            "note": "nested job creation is currently advisory in runtime tool mode",
        }

    if tool_name == "list_uploaded_context_files":
        store = _safe_upload_store(workspace)
        files = [
            {
                "name": path.name,
                "size": path.stat().st_size,
            }
            for path in sorted(store.glob("*"))
            if path.is_file()
        ]
        return {"tool_name": tool_name, "files": files}

    if tool_name == "get_uploaded_context_file":
        name = str(constraints.get("name") or "").strip()
        target = _safe_workspace_target(_safe_upload_store(workspace), name)
        if not target or not target.exists() or not target.is_file():
            return {"tool_name": tool_name, "error": "uploaded context file not found"}
        return {
            "tool_name": tool_name,
            "name": name,
            "content": target.read_text(encoding="utf-8", errors="replace")[:12000],
        }

    if tool_name == "clear_uploaded_context_store":
        store = _safe_upload_store(workspace)
        deleted = 0
        for path in store.glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)
                deleted += 1
        return {"tool_name": tool_name, "deleted_files": deleted}

    if tool_name == "retrieve_conversation_summary":
        context_state = session.context_state or {}
        summary = context_state.get("summary")
        messages = context_state.get("messages") if isinstance(context_state.get("messages"), list) else []
        preview = []
        for item in messages[-5:]:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")
            if role and content:
                preview.append(f"{role}: {content[:240]}")
        return {
            "tool_name": tool_name,
            "summary": summary,
            "recent_messages": preview,
        }

    return {
        "tool_name": tool_name,
        "status": "not_implemented",
        "message": "builtin tool handler not implemented",
    }


def _render_shell_command(template: str, constraints: dict, workspace: Path) -> str:
    values = {"workspace": str(workspace)}
    for key, value in (constraints or {}).items():
        if isinstance(value, (dict, list)):
            values[key] = json.dumps(value)
        else:
            values[key] = str(value)

    class _Default(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(_Default(values)).strip()


async def _run_tool(
    tool_name: str,
    session: SessionModel,
    job: JobModel,
    call_constraints: dict | None = None,
) -> dict:
    workspace = _safe_workspace_path(session.workspace_path)
    constraints = dict(job.constraints or {})
    if isinstance(call_constraints, dict):
        constraints.update(call_constraints)
    tool = tool_registry.get(tool_name)
    executor = tool.executor if tool else None

    if not isinstance(executor, dict):
        return {
            "tool_name": tool_name,
            "status": "not_implemented",
            "message": "missing executor config in tools registry",
        }

    exec_kind = str(executor.get("kind") or "builtin")
    if exec_kind == "builtin":
        builtin_name = str(executor.get("name") or tool_name)
        return _run_builtin_tool(builtin_name, workspace, constraints, session, job)

    if exec_kind == "shell":
        template = str(executor.get("command") or "").strip()
        if not template:
            return {"tool_name": tool_name, "error": "executor.command is required for shell tools"}
        command = _render_shell_command(template, constraints, workspace)
        if not command:
            return {"tool_name": tool_name, "error": "rendered command was empty"}
        timeout_s = min(
            120,
            max(1, int(executor.get("timeout_s") or constraints.get("timeout_s") or 30)),
        )
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return {
                "tool_name": tool_name,
                "command": command,
                "error": f"command timed out after {timeout_s}s",
            }

        return {
            "tool_name": tool_name,
            "command": command,
            "exit_code": process.returncode,
            "stdout": (stdout or b"").decode("utf-8", errors="replace")[:12000],
            "stderr": (stderr or b"").decode("utf-8", errors="replace")[:8000],
        }

    return {
        "tool_name": tool_name,
        "status": "not_implemented",
        "message": f"unsupported executor kind: {exec_kind}",
    }


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

        tool = tool_registry.get(requested_tool)
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
                skills_hint = ",".join(enabled_skills) if isinstance(enabled_skills, list) and enabled_skills else "none"
                tools_hint = ",".join(enabled_tools) if isinstance(enabled_tools, list) and enabled_tools else "all"
                system_prompt_override = context_state.get("system_prompt_override")
                rules_checklist = context_state.get("rules_checklist")
                chat_mode = (session.mode or "").lower() == "chat"

                all_tools = tool_registry.list_tools()
                citations_required = _citations_required(
                    session.mode,
                    enabled_tools if isinstance(enabled_tools, list) else None,
                    {tool.name for tool in all_tools},
                )

                context_messages = context_state.get("messages") if isinstance(context_state.get("messages"), list) else []
                max_context_messages = max(5, int(context_state.get("max_context_messages", 40)))
                history_window = min(20, max_context_messages)
                recent_context = context_messages[-history_window:] if history_window else []
                context_lines = [
                    f"{(item.get('role') or 'unknown')}: {item.get('content') or ''}"
                    for item in recent_context
                    if _is_user_facing_context_message(item)
                ]
                context_block = "\n".join(context_lines).strip()
                max_context_chars = max(1000, int(context_state.get("max_context_chars", 8000)))
                if len(context_block) > max_context_chars:
                    context_block = context_block[-max_context_chars:]

                all_skills = skill_registry.discover(session.workspace_path)
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

                tools_reference_block = "\n".join(tool_reference_lines)
                skills_reference_block = "\n".join(skill_reference_lines) if skill_reference_lines else "- none"

                stage_output = ""
                stage_feedback = ""
                last_provider = ""
                prior_normalized_outputs: list[str] = []
                max_stage_turns = max(1, int(context_state.get("max_stage_turns", DEFAULT_MAX_STAGE_TURNS)))
                for stage_attempt in range(1, max_stage_turns + 1):
                    stage_success = "\n".join([f"- {item}" for item in step.success_criteria])
                    if chat_mode:
                        prompt = f"goal={job.goal}\nmode={session.mode}"
                        prompt += "\n\nchat_protocol:\n"
                        prompt += "- Respond directly to the user in normal chat form.\n"
                        prompt += "- Do not include stage markers or agent loop narration.\n"
                        prompt += "- Do not suggest or simulate tool calls unless explicitly asked for that behavior."
                    else:
                        prompt = f"goal={job.goal}\nmode={session.mode}\nstep={step.label}\nenabled_skills={skills_hint}\nenabled_tools={tools_hint}"
                        prompt += "\n\nstage_objective:\n" + step.objective
                        prompt += "\n\nstage_success_criteria:\n" + stage_success
                        prompt += "\n\navailable_tools_by_name_and_usage:\n" + tools_reference_block
                        prompt += "\n\navailable_skills_by_name_and_usage:\n" + skills_reference_block
                        prompt += (
                            "\n\nstage_protocol:\n"
                            f"- When this stage is complete, prefix your response with {STAGE_READY_PREFIX}{step.label}::\n"
                            f"- If not complete, prefix with {STAGE_NEEDS_MORE_PREFIX}{step.label}:: and explain what is missing.\n"
                            "- Do not omit this prefix."
                            "\n- Use STAGE_NEEDS_MORE when criteria are not yet satisfied; do not use STAGE_READY prematurely."
                        )
                        prompt += f"\ncurrent_stage_attempt={stage_attempt}/{max_stage_turns}"
                        if stage_feedback:
                            prompt += f"\n\nstage_feedback:\n{stage_feedback}"

                    if citations_required:
                        prompt += (
                            "\n\ncitation_requirements:\n"
                            "- Any claim derived from external web/PDF/arXiv/link sources MUST include an inline citation like [1].\n"
                            "- Inline citations must use markdown links to the source URL, e.g. [1](https://example.com/source).\n"
                            "- Include a separate `## Citations` section at the end listing each referenced URL.\n"
                            "- Do not present externally sourced claims without citations."
                        )
                    if context_block:
                        prompt += f"\n\nconversation_context:\n{context_block}"
                    if isinstance(system_prompt_override, str) and system_prompt_override.strip():
                        prompt += f"\n\nsystem_prompt_override={system_prompt_override.strip()}"
                    if isinstance(rules_checklist, str) and rules_checklist.strip():
                        prompt += f"\n\nrules_checklist={rules_checklist.strip()}"

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
                    if requested_tool_calls:
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
                                f"tool_results={json.dumps(tool_results, ensure_ascii=False)}"
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
                    raise RuntimeError(
                        f"Stage '{step.label}' did not provide required readiness confirmation "
                        f"after {max_stage_turns} attempts"
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
