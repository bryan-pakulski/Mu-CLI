"""Artifact list/download/delete endpoints."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from mu.artifact import ArtifactRegistry
from utils.config import HISTORY_DIR

router = APIRouter()


def _registry(session_name: str) -> ArtifactRegistry:
    safe = str(session_name or "").strip()
    if not safe or os.path.basename(safe) != safe or safe in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid session name")
    session_dir = os.path.join(HISTORY_DIR, "sessions", safe)
    if not os.path.isdir(session_dir):
        raise HTTPException(status_code=404, detail="session not found")
    return ArtifactRegistry(session_dir)


@router.get("/{name}/artifacts")
async def list_artifacts(name: str):
    return {"artifacts": _registry(name).list()}


@router.get("/{name}/artifacts/{artifact_id}/download")
async def download_artifact(name: str, artifact_id: str):
    registry = _registry(name)
    descriptor = registry.get(artifact_id)
    path = registry.resolve_path(artifact_id)
    if descriptor is None or path is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(
        path,
        media_type=descriptor.get("mime_type") or "application/octet-stream",
        filename=descriptor.get("name") or "artifact",
    )


@router.delete("/{name}/artifacts/{artifact_id}")
async def delete_artifact(name: str, artifact_id: str, request: Request):
    session = request.app.state.session_by_name(name)
    if session is not None and request.app.state.session_busy_for(name).is_set():
        raise HTTPException(status_code=409, detail="cannot delete artifacts during an active turn")
    if not _registry(name).remove(artifact_id):
        raise HTTPException(status_code=404, detail="artifact not found")
    return {"ok": True, "artifact_id": artifact_id}
