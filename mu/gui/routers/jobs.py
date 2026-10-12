"""Durable engineering-job API shared by the browser GUI and mobile client."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response

from mu.jobs import AttentionReason, JobService, JobStateError, JobStatus
from mu.jobs.board import build_job_board
from mu.jobs.diagnostics import build_job_diagnostics
from mu.jobs.management import JobManagementError, JobManagementService
from mu.jobs.receipt import JobReceiptBuilder
from mu.jobs.repository import RepositoryRegistry
from mu.jobs.review import JobReviewError, JobReviewService, build_job_diff
from mu.jobs.verification import VerificationStore


router = APIRouter()


def _service(request: Request) -> JobService:
    service = getattr(request.app.state, "job_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="job service is unavailable")
    return service


def _job_or_404(service: JobService, job_id: str):
    try:
        return service.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc


def _state_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (JobStateError, JobReviewError, JobManagementError, ValueError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _history_filters(
    *,
    q: str = "",
    status: Optional[List[str]] = None,
    repository: str = "",
    archive: str = "all",
    scope: str = "history",
    created_after: Optional[float] = None,
    created_before: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "q": q,
        "statuses": status,
        "repository": repository,
        "archive": archive,
        "scope": scope,
        "created_after": created_after,
        "created_before": created_before,
    }


def _with_session_defaults(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Allow GUI/mobile job creation to inherit the currently loaded session.

    The durable job still stores explicit provider/model/workspace values; this
    is only a control-plane convenience at creation time.
    """
    value = dict(payload or {})
    session_name = str(value.get("session_name") or "").strip()
    if not session_name:
        return value
    resolver = getattr(request.app.state, "session_by_name", None)
    session = resolver(session_name) if callable(resolver) else None
    if session is None:
        raise HTTPException(status_code=409, detail=f"Session '{session_name}' is not loaded")

    folders = list(getattr(getattr(session, "folder_context", None), "folders", []) or [])
    if not str(value.get("workspace_root") or "").strip() and folders:
        value["workspace_root"] = folders[0]
    execution = dict(value.get("execution") or {})
    provider = getattr(getattr(session, "provider", None), "name", "") or ""
    model = getattr(getattr(session, "provider", None), "model_name", "") or ""
    variables = getattr(session, "variables", {}) or {}
    # Precedence: explicit job/stage values -> Job settings stage models ->
    # loaded session. Never stamp the session model as the job-level default:
    # resolve_stage_model ranks that above settings, so every stage would run
    # on whatever model the session happened to use (e.g. ollama glm-5.3
    # despite Anthropic stage models in settings).
    if not execution.get("model") and provider and model:
        from mu.jobs import job_settings

        stages = {k: dict(v or {}) for k, v in dict(execution.get("stages") or {}).items()}
        for stage in job_settings.missing_stages(execution):
            stages[stage] = {"provider": str(provider), "model": str(model)}
        if stages:
            execution["stages"] = stages
    execution.setdefault("agent_mode", str(variables.get("agent_mode", "default") or "default"))
    execution.setdefault("session_type", str(variables.get("session_type", "workspace") or "workspace"))
    execution.setdefault("auto_approve_writes", bool(variables.get("yolo", False)))
    value["execution"] = execution
    return value


@router.get("")
async def list_jobs(
    request: Request,
    status: Optional[List[str]] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    service = _service(request)
    try:
        jobs = service.list(statuses=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"jobs": [job.to_dict() for job in jobs]}


@router.post("")
async def create_job(request: Request, payload: Dict[str, Any]):
    service = _service(request)
    try:
        job = service.create_from_payload(_with_session_defaults(request, payload))
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job": job.to_dict()}


@router.get("/attention")
async def get_job_attention(request: Request):
    """Aggregate "needs you" count + rows (NEEDS_HUMAN / CONFLICTED)."""
    return _service(request).attention_summary()


@router.get("/board")
async def get_job_board(request: Request):
    return build_job_board(_service(request)).to_dict()


@router.get("/history")
async def query_job_history(
    request: Request,
    q: str = Query(default="", max_length=300),
    status: Optional[List[str]] = Query(default=None),
    repository: str = Query(default="", max_length=1000),
    archive: str = Query(default="all"),
    scope: str = Query(default="history"),
    created_after: Optional[float] = Query(default=None),
    created_before: Optional[float] = Query(default=None),
    sort: str = Query(default="updated"),
    order: str = Query(default="desc"),
    limit: int = Query(default=100, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
):
    try:
        return JobManagementService(_service(request)).query_jobs(
            **_history_filters(
                q=q,
                status=status,
                repository=repository,
                archive=archive,
                scope=scope,
                created_after=created_after,
                created_before=created_before,
            ),
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
    except (ValueError, JobManagementError) as exc:
        raise _state_error(exc) from exc


@router.get("/report")
async def get_job_report(
    request: Request,
    q: str = Query(default="", max_length=300),
    status: Optional[List[str]] = Query(default=None),
    repository: str = Query(default="", max_length=1000),
    archive: str = Query(default="all"),
    scope: str = Query(default="history"),
    created_after: Optional[float] = Query(default=None),
    created_before: Optional[float] = Query(default=None),
):
    try:
        return {
            "report": JobManagementService(_service(request)).report(
                **_history_filters(
                    q=q,
                    status=status,
                    repository=repository,
                    archive=archive,
                    scope=scope,
                    created_after=created_after,
                    created_before=created_before,
                )
            )
        }
    except (ValueError, JobManagementError) as exc:
        raise _state_error(exc) from exc


@router.get("/report/export")
async def export_job_report(
    request: Request,
    format: str = Query(default="json"),
    q: str = Query(default="", max_length=300),
    status: Optional[List[str]] = Query(default=None),
    repository: str = Query(default="", max_length=1000),
    archive: str = Query(default="all"),
    scope: str = Query(default="history"),
    created_after: Optional[float] = Query(default=None),
    created_before: Optional[float] = Query(default=None),
):
    try:
        content, media_type, filename = JobManagementService(_service(request)).report_export(
            format=format,
            **_history_filters(
                q=q,
                status=status,
                repository=repository,
                archive=archive,
                scope=scope,
                created_after=created_after,
                created_before=created_before,
            ),
        )
    except (ValueError, JobManagementError) as exc:
        raise _state_error(exc) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/manage/bulk")
async def bulk_manage_jobs(request: Request, payload: Dict[str, Any]):
    try:
        return JobManagementService(_service(request)).bulk(
            str(payload.get("action") or ""),
            list(payload.get("job_ids") or []),
            reason=str(payload.get("reason") or ""),
            force=bool(payload.get("force", False)),
        )
    except (ValueError, JobManagementError) as exc:
        raise _state_error(exc) from exc


@router.post("/jira/import")
async def import_jira_issues(request: Request, payload: Dict[str, Any]):
    """Create durable jobs from Jira issues (keys and/or JQL).

    Credentials come from the server environment only; any credential keys in
    the request body are rejected so tokens never reach the job record.
    """
    from mu.integrations.jira import JiraClient, JiraConfig, JiraError, import_issues

    body = dict(payload or {})
    for forbidden in ("api_token", "token", "pat", "password", "JIRA_API_TOKEN", "JIRA_PAT"):
        if forbidden in body:
            raise HTTPException(status_code=400, detail="Jira credentials are read from the server environment only")
    keys = body.get("keys") or ([body["key"]] if body.get("key") else [])
    if isinstance(keys, str):
        keys = [k for k in keys.replace(",", " ").split() if k]
    jql = str(body.get("jql") or "").strip()
    if not keys and not jql:
        raise HTTPException(status_code=400, detail="Provide 'keys' or 'jql'")
    try:
        client = _jira_client()
        raw_defaults = dict(body.get("defaults") or {})
        # The Work page sends ``session_name`` at the top level of the body;
        # without forwarding it the job got no workspace_root and the worker
        # silently fell back to the GUI process's own repository.
        if body.get("session_name") and not raw_defaults.get("session_name"):
            raw_defaults["session_name"] = body.get("session_name")
        defaults = _with_session_defaults(request, raw_defaults)
        if not str(defaults.get("workspace_root") or "").strip():
            raise HTTPException(
                status_code=400,
                detail="No workspace for imported jobs: open Work from a session with an attached workspace.",
            )
        result = import_issues(
            _service(request), client, keys=keys, jql=jql, defaults=defaults,
            max_results=int(body.get("max_results") or 20),
        )
    except JiraError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


def _jira_client():
    """Client from the saved link ($MUCLI_HOME/integrations/jira.json) + env overrides."""
    from mu.integrations.jira import JiraClient, JiraError
    from mu.integrations.jira_settings import resolve_config

    try:
        config = resolve_config()
    except JiraError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not (config.base_url and config.mode):
        raise HTTPException(status_code=400, detail="Jira is not linked. Add the URL and token in Job settings.")
    return JiraClient(config)


def _jira_call(fn, *args, **kwargs):
    from mu.integrations.jira import JiraError

    try:
        return fn(*args, **kwargs)
    except JiraError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/settings")
async def get_job_settings():
    """Pipeline settings: per-stage provider/model defaults + plan approval gate."""
    from mu.jobs.job_settings import public_settings

    return public_settings()


@router.put("/settings")
async def put_job_settings(payload: Dict[str, Any]):
    from mu.jobs.job_settings import public_settings, save_settings

    try:
        save_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_settings()


@router.get("/jira/settings")
async def get_jira_settings():
    """Secret-free link status + browsing preferences."""
    from mu.integrations.jira_settings import public_settings

    return public_settings()


@router.put("/jira/settings")
async def put_jira_settings(payload: Dict[str, Any]):
    """Save link fields/token/preferences. Blank token keeps the stored one."""
    from mu.integrations.jira import JiraError
    from mu.integrations.jira_settings import save_settings

    try:
        return save_settings(dict(payload or {}))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JiraError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/jira/settings")
async def delete_jira_settings():
    """Unlink: drop URL/email/tokens, keep preferred boards and JQL."""
    from mu.integrations.jira_settings import clear_settings

    return clear_settings(keep_preferences=True)


@router.post("/jira/test")
async def test_jira_link():
    client = _jira_client()
    return {"ok": True, "user": _jira_call(client.myself), "jira": client.config.public()}


@router.get("/jira/boards")
async def list_jira_boards(query: str = ""):
    from mu.integrations.jira_settings import public_settings

    client = _jira_client()
    boards = _jira_call(client.list_boards, query=query)
    preferred = {b["id"] for b in public_settings()["preferences"]["boards"]}
    # Suggest boards for projects where I have open assigned work.
    mine = client.my_project_keys()
    rank = {key: i for i, key in enumerate(mine)}
    for board in boards:
        board["preferred"] = board.get("id") in preferred
        board["suggested"] = board.get("project", "") in rank
    boards.sort(key=lambda b: (
        not b["preferred"], not b["suggested"],
        rank.get(b.get("project", ""), 1 << 20), str(b.get("name", "")).lower(),
    ))
    return {"boards": boards, "my_projects": mine}


@router.get("/jira/boards/{board_id}/sprints")
async def list_jira_sprints(board_id: int, state: str = "active,future"):
    client = _jira_client()
    return {"sprints": _jira_call(client.list_sprints, board_id, state=state)}


@router.get("/jira/tickets")
async def list_jira_tickets(
    request: Request,
    board_id: Optional[int] = None,
    sprint_id: Optional[int] = None,
    jql: str = "",
    mine: bool = False,
    backlog: bool = False,
    include_done: bool = False,
    status: str = "",
    max_results: int = 300,
):
    """Tickets for a board/sprint/backlog or a JQL search, each with its linked job + status.

    Done/closed tickets (status category Done) are hidden unless
    ``include_done``; ``status`` is an optional comma-separated status-name
    filter. All pages are fetched (Jira caps pages at 50).
    """
    from mu.integrations.jira import build_ticket_jql, linked_jobs, status_counts, ticket_summary
    from mu.integrations.jira_settings import public_settings

    client = _jira_client()
    statuses = [s.strip() for s in str(status or "").split(",") if s.strip()]
    user_jql = str(jql or "").strip()
    if board_id or sprint_id:
        query = build_ticket_jql(user_jql, mine=mine, include_done=include_done, statuses=statuses)
        issues = _jira_call(client.board_issues, board_id or 0, sprint_id=sprint_id, jql=query,
                            max_results=max_results, backlog=backlog and not sprint_id)
    else:
        user_jql = user_jql or public_settings()["preferences"]["default_jql"]
        if not user_jql and not mine:
            raise HTTPException(status_code=400, detail="Pick a board/sprint or provide a JQL query")
        query = build_ticket_jql(user_jql, mine=mine, include_done=include_done, statuses=statuses)
        issues = _jira_call(client.search, query, max_results=max_results)
    linked = linked_jobs(_service(request))
    base = client.config.base_url
    tickets = [ticket_summary(i, base_url=base, linked=linked) for i in issues]
    return {"tickets": tickets, "base_url": base, "statuses": status_counts(tickets),
            "include_done": include_done, "jql": query}


@router.get("/repositories")
async def list_job_repositories(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
):
    registry = RepositoryRegistry(_service(request).store)
    return {"repositories": [item.to_dict() for item in registry.list(limit=limit)]}


@router.get("/repositories/{repository_id}")
async def get_job_repository(repository_id: str, request: Request):
    registry = RepositoryRegistry(_service(request).store)
    try:
        record = registry.get(repository_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Repository '{repository_id}' not found") from exc
    return {"repository": record.to_dict()}


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    service = _service(request)
    job = _job_or_404(service, job_id)
    management = JobManagementService(service).state(job.id)
    return {"job": {**job.to_dict(), **management}}


@router.post("/{job_id}/archive")
async def archive_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    try:
        state = JobManagementService(_service(request)).archive(
            job_id,
            reason=str((payload or {}).get("reason") or ""),
        )
    except (KeyError, ValueError, JobManagementError) as exc:
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc
        raise _state_error(exc) from exc
    return {"job_id": job_id, **state}


@router.post("/{job_id}/restore")
async def restore_job(job_id: str, request: Request):
    try:
        state = JobManagementService(_service(request)).restore(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc
    return {"job_id": job_id, **state}


@router.delete("/{job_id}")
async def delete_historic_job(
    job_id: str,
    request: Request,
    purge_artifacts: bool = Query(default=True),
    force: bool = Query(
        default=False,
        description="Cancel an active job first, auto-archive, then purge (single-step delete).",
    ),
):
    try:
        return JobManagementService(_service(request)).delete(
            job_id,
            purge_artifacts=purge_artifacts,
            force=force,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc
    except (ValueError, JobManagementError) as exc:
        raise _state_error(exc) from exc


@router.get("/{job_id}/receipt")
async def get_job_receipt(job_id: str, request: Request):
    service = _service(request)
    _job_or_404(service, job_id)
    return {"receipt": JobReceiptBuilder(service).build(job_id)}


@router.get("/{job_id}/diagnostics")
async def get_job_diagnostics(
    job_id: str,
    request: Request,
    event_limit: int = Query(default=200, ge=1, le=1000),
    log_tail_bytes: int = Query(default=65_536, ge=1024, le=524_288),
):
    service = _service(request)
    _job_or_404(service, job_id)
    return {
        "diagnostics": build_job_diagnostics(
            service,
            job_id,
            event_limit=event_limit,
            log_tail_bytes=log_tail_bytes,
        ).to_dict()
    }


@router.get("/{job_id}/debug-export")
async def export_job_debug_bundle(job_id: str, request: Request):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        content = JobManagementService(service).debug_bundle(job_id)
    except (JobManagementError, ValueError) as exc:
        raise _state_error(exc) from exc
    filename = f"mucli-job-{job_id[:12]}-debug.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{job_id}/diff")
async def get_job_diff(
    job_id: str,
    request: Request,
    max_chars: int = Query(default=500_000, ge=10_000, le=2_000_000),
):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        return {"diff": build_job_diff(service, job_id, max_chars=max_chars).to_dict()}
    except JobReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{job_id}/events")
async def get_job_events(
    job_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
):
    service = _service(request)
    _job_or_404(service, job_id)
    return {"events": [event.to_dict() for event in service.events(job_id, after_id=after, limit=limit)]}


@router.get("/{job_id}/attempts")
async def get_job_attempts(job_id: str, request: Request):
    service = _service(request)
    _job_or_404(service, job_id)
    return {"attempts": [attempt.to_dict() for attempt in service.attempts(job_id)]}


@router.get("/{job_id}/verifications")
async def get_job_verifications(
    job_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
):
    service = _service(request)
    _job_or_404(service, job_id)
    values = VerificationStore(service.store).list(job_id, limit=limit)
    return {"verifications": [value.to_dict() for value in values]}


@router.get("/{job_id}/verifications/{verification_id}")
async def get_job_verification(job_id: str, verification_id: str, request: Request):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        value = VerificationStore(service.store).get(verification_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Verification '{verification_id}' not found") from exc
    if value.job_id != job_id:
        raise HTTPException(status_code=404, detail=f"Verification '{verification_id}' not found for job")
    return {"verification": value.to_dict()}


@router.get("/{job_id}/drift")
async def get_job_base_drift(job_id: str, request: Request, apply: bool = Query(default=False)):
    """Base-drift + mergeability of the review branch. ``apply=true`` also
    records the assessment and flips READY_FOR_REVIEW -> CONFLICTED when the
    branch no longer merges."""
    from mu.jobs.base_drift import BaseDriftError, apply_base_drift, assess_base_drift

    service = _service(request)
    _job_or_404(service, job_id)
    try:
        report = apply_base_drift(service, job_id, source="api") if apply else assess_base_drift(service, job_id)
    except BaseDriftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"drift": report.to_dict(), "job": service.get(job_id).to_dict()}


@router.post("/{job_id}/respond")
async def respond_to_job(job_id: str, request: Request, payload: Dict[str, Any]):
    decision = payload.get("decision")
    if decision == "approve":
        return _core_command(request, job_id, "approvePlan", payload)
    if decision == "replan":
        return _core_command(request, job_id, "rejectPlan", {"feedback": payload.get("detail")})
    return _core_command(request, job_id, "resume", {"guidance": payload.get("detail")})


def _core_command(request, job_id, kind, args):
    """Compatibility URL only: authorization and mutation belong to core."""
    from mu.jobs.core.commands import CommandError, CommandRejected, dispatch
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        dispatch(service, {"type": kind, "job_id": job_id, "args": args or {}})
    except CommandRejected as exc:
        raise HTTPException(409, str(exc)) from exc
    except CommandError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"job": service.get(job_id).to_dict()}


@router.post("/{job_id}/request-changes")
async def request_job_changes(job_id: str, request: Request, payload: Dict[str, Any]):
    return _core_command(request, job_id, "reprompt", {"finding_ids": payload.get("finding_ids"), "guidance": payload.get("feedback")})


@router.post("/{job_id}/continue")
async def continue_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    return _core_command(request, job_id, "push", payload)


@router.post("/{job_id}/discard")
async def discard_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    return _core_command(request, job_id, "discard", payload)


@router.post("/{job_id}/transition")
async def transition_job(job_id: str, request: Request, payload: Dict[str, Any]):
    raise HTTPException(410, "Direct status mutations are retired. Use /api/jobs/cmd and the job's available actions.")


# Compatibility controls retained while clients migrate to the review actions.
@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    return _core_command(request, job_id, "discard", payload)


@router.post("/{job_id}/attention")
async def require_attention(job_id: str, request: Request, payload: Dict[str, Any]):
    raise HTTPException(410, "Stage attention is managed by the worker. Use /api/jobs/cmd to message or resume it.")


@router.post("/{job_id}/resume")
async def resume_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    return _core_command(request, job_id, "resume", {"guidance": (payload or {}).get("detail")})


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    return _core_command(request, job_id, "retry", payload)
