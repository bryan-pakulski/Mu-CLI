"""Security-mode introspection.

Surfaces the active SecurityReport so the GUI can render findings with
their proof/remediation workflow state. Reports live on disk under
``documentation/security_scan_<scan_id>/report.json`` (workspace) or
``~/.mucli/sessions/<id>/security/`` (session fallback).

Discovery: the in-memory session may hold ``_active_security_report``
(set by the security tool handlers). When nothing is in memory we fall
back to scanning the workspace's ``documentation/`` tree for the most
recently modified report.json.

Read-only by default. The two mutation endpoints (approve / refute)
take the per-session lock to serialize with concurrent chat turns.
"""

from __future__ import annotations

import glob
import json
import logging
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from mu.security.engine import (
    SEVERITY_LEVELS,
    SecurityReport,
    approve_finding,
    load_report,
    refute_finding,
    save_report,
    summarize_report,
)

router = APIRouter()
_logger = logging.getLogger(__name__)


def _discover_report(session) -> Optional[SecurityReport]:
    """Try to find an active SecurityReport for the given session.

    1. In-memory (set by the security tool handlers during a turn).
    2. Workspace glob: most recently modified report.json under
       ``documentation/security_scan_*/``.
    """
    report = getattr(session, "_active_security_report", None)
    if isinstance(report, SecurityReport):
        return report

    sm = session.session_manager
    folders = getattr(sm.folder_context, "folders", []) or []
    workspace = folders[0] if folders else None
    if not workspace:
        return None

    pattern = os.path.join(workspace, "documentation", "security_scan_*", "report.json")
    candidates = glob.glob(pattern)
    if not candidates:
        return None

    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for path in candidates:
        report = load_report(path)
        if report is not None:
            return report
    return None


def _finding_payload(f) -> Dict[str, Any]:
    """Compact finding dict for the panel — avoids shipping raw stdout."""
    proof = f.proof
    remediation = f.remediation
    return {
        "finding_id": f.finding_id,
        "title": f.title,
        "summary": f.summary,
        "vulnerability_class": f.vulnerability_class,
        "severity": f.severity,
        "affected_paths": list(f.affected_paths or []),
        "exploit_path": f.exploit_path,
        "references": list(f.references or []),
        "status": f.status,
        "created_at": f.created_at,
        "updated_at": f.updated_at,
        "has_proof": proof is not None,
        "proof_verified": bool(proof and proof.verified),
        "proof_command": (proof.command if proof else ""),
        "proof_description": (proof.description if proof else ""),
        "proof_verified_at": (proof.verified_at if proof else None),
        "has_remediation": remediation is not None,
        "remediation_verified": bool(remediation and remediation.verified),
        "remediation_description": (remediation.description if remediation else ""),
        "remediation_verified_at": (remediation.verified_at if remediation else None),
        "patch_diff": (remediation.patch_diff if remediation else ""),
    }


@router.get("/state")
async def get_security_state(request: Request) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        return {
            "active": False,
            "report": None,
            "findings": [],
            "summary": None,
        }

    report = _discover_report(session)
    if report is None:
        return {
            "active": True,
            "report": None,
            "findings": [],
            "summary": None,
        }

    findings = [_finding_payload(f) for f in report.findings]
    summary = summarize_report(report)

    return {
        "active": True,
        "report": {
            "scan_id": report.scan_id,
            "title": report.title,
            "summary": report.summary,
            "status": report.status,
            "directory": report.directory,
            "metadata_path": report.metadata_path,
            "findings_total": len(report.findings),
            "created_at": report.created_at,
            "updated_at": report.updated_at,
        },
        "findings": findings,
        "summary": summary,
    }


class ApproveBody(BaseModel):
    pass


class RefuteBody(BaseModel):
    reason: str = ""


@router.post("/findings/{finding_id}/approve")
async def approve(request: Request, finding_id: str) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")

    report = _discover_report(session)
    if report is None:
        raise HTTPException(status_code=404, detail="no active security report")

    lock = request.app.state.session_lock_for()
    with lock:
        finding = report.find(finding_id)
        if finding is None:
            raise HTTPException(status_code=404, detail=f"finding {finding_id} not found")
        try:
            approve_finding(finding)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        save_report("", report)

    return {"ok": True, "finding_id": finding_id, "status": finding.status}


@router.post("/findings/{finding_id}/refute")
async def refute(request: Request, finding_id: str, body: RefuteBody) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")

    report = _discover_report(session)
    if report is None:
        raise HTTPException(status_code=404, detail="no active security report")

    lock = request.app.state.session_lock_for()
    with lock:
        finding = report.find(finding_id)
        if finding is None:
            raise HTTPException(status_code=404, detail=f"finding {finding_id} not found")
        try:
            refute_finding(finding, reason=body.reason)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        save_report("", report)

    return {"ok": True, "finding_id": finding_id, "status": finding.status}
