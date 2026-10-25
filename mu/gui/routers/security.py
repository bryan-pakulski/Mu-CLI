"""Session-scoped security state and operator decisions. PoCs stay in tool approvals."""
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from mu.modes.evidence import CheckRefused
from mu.security import engine
from mu.security.state import security_state
from ..mode_session import mode_session, mode_session_lock
from ..mode_workspace import security_workspace
from .mode_state import save, states_for, surface_actor

router = APIRouter()


def _run_payload(run):
    from mu.modes.evidence import describe_predicate
    data = dict(run)
    data.pop("log_path", None)
    data["security_output_path"] = str(run.get("evidence_path", "")) + "/output.txt"
    data["predicate_text"] = describe_predicate(run.get("predicate") or {})
    return data


def _finding_payload(f):
    data = asdict(f)
    data["runs"] = [_run_payload(r) for r in f.runs]
    proof, rem = f.proof, f.remediation
    data.update(has_proof=proof is not None, proof_verified=bool(proof and proof.verified),
                proof_command=proof.command if proof else "", proof_description=proof.description if proof else "",
                proof_verified_at=proof.verified_at if proof else None, proof_notes=proof.notes if proof else "",
                has_remediation=rem is not None, remediation_verified=bool(rem and rem.verified),
                remediation_description=rem.description if rem else "",
                remediation_verified_at=rem.verified_at if rem else None, remediation_notes=rem.notes if rem else "", patch_diff=rem.patch_diff if rem else "")
    data.pop("proof")
    data.pop("remediation")
    return data


def security_payload(session):
    active = bool(session and session.variables.get("agent_mode") == "security")
    data = {"active": active, "report": None, "findings": [], "summary": None, "surface": [],
            "observations": [], "runs": [], "report_markdown": "", "runner": "docker", "host_allowed": False,
            "plan_mode": bool(session and session.variables.get("plan_mode")), "blockers": []}
    report = engine.active_report(session, required=False) if session else None
    if report:
        summary = engine.summarize_report(report)
        data.update(report={**summary, "summary": report.summary, "created_at": report.created_at, "updated_at": report.updated_at},
                    summary=summary, findings=[_finding_payload(f) for f in report.findings],
                    surface=report.surface, observations=report.observations,
                    runs=[_run_payload(r) for f in report.findings for r in f.runs], runner=engine.runner_for(session, report),
                    host_allowed=security_state(session).execution.get(report.scan_id, {}).get("host_allowed", False))
        path = engine.safe_path(report.workspace_root, str(Path(report.directory, "REPORT.md")), exists=False)
        if Path(path).is_file():
            data["report_markdown"] = Path(path).read_text()
    data["workspace"] = security_workspace(data["report"], data["findings"], active=active)
    return data


@router.get("/state")
async def get_security_state(request: Request) -> dict[str, Any]:
    session = mode_session(request)
    # A chat turn holds the session lock while waiting for approval. Reads must
    # remain available so the panel can show the very decision it is waiting on.
    return security_payload(session)


def _mutate(request, action):
    session, _ = states_for(request)
    with mode_session_lock(request, session):
        try:
            engine.writable(session)
            report = engine.active_report(session)
            action(session, report)
            engine.persist(session, report)
            save(session)
        except (ValueError, CheckRefused) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return security_payload(session)


class RefuteBody(BaseModel):
    reason: str


@router.post("/findings/{finding_id}/approve")
async def approve(request: Request, finding_id: str):
    def action(session, report):
        engine.approve_finding(engine.require_finding(report, finding_id), report=report, owner=session)
        report.events.append({"action": "approve", "finding_id": finding_id, "actor": surface_actor(request)})
    data = _mutate(request, action)
    return {**data, "ok": True, "finding_id": finding_id, "status": "approved"}


@router.post("/findings/{finding_id}/refute")
async def refute(request: Request, finding_id: str, body: RefuteBody):
    def action(session, report):
        engine.refute_finding(engine.require_finding(report, finding_id), reason=body.reason)
        report.events.append({"action": "refute", "finding_id": finding_id, "actor": surface_actor(request)})
    data = _mutate(request, action)
    return {**data, "ok": True, "finding_id": finding_id, "status": "refuted"}


class RunnerBody(BaseModel):
    runner: str


@router.post("/runner")
async def runner(request: Request, body: RunnerBody):
    return _mutate(request, lambda session, report: engine.configure_runner(session, body.runner, actor=surface_actor(request)))


@router.post("/report")
async def report(request: Request):
    return _mutate(request, lambda session, report: engine.generate_report(report))


class OpenBody(BaseModel):
    scan_id: str


@router.post("/open")
async def open_scan(request: Request, body: OpenBody):
    session, _ = states_for(request)
    with mode_session_lock(request, session):
        try:
            engine.open_report(session, body.scan_id)
            save(session)
        except (ValueError, CheckRefused) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return security_payload(session)


@router.get("/artifacts/{artifact_path:path}")
async def artifact(request: Request, artifact_path: str):
    """Read only generated evidence inside the selected session's active scan."""
    import re
    session, _ = states_for(request)
    try:
        report = engine.active_report(session)
        allowed = (artifact_path == "REPORT.md" or
                   re.fullmatch(r"findings/f[0-9]+\.md|patches/f[0-9]+\.diff|proofs/f[0-9]+/r[0-9]+-[a-f0-9]+/(?:output\.txt|run\.json|bundle\.json)", artifact_path))
        if not allowed:
            raise ValueError("unknown security artifact")
        path = engine.safe_path(report.directory, artifact_path)
        return {"path": artifact_path, "text": Path(path).read_text(),
                "format": "markdown" if path.endswith(".md") else "text"}
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=404, detail="security artifact not found") from exc
