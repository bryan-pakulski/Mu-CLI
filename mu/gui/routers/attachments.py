
"""Upload/list/download/delete endpoints for session attachments."""
from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from mu.attachment import AttachmentError, AttachmentRegistry
from utils.config import HISTORY_DIR

router = APIRouter()


def _registry(session_name: str) -> AttachmentRegistry:
    safe = str(session_name or "").strip()
    if not safe or os.path.basename(safe) != safe or safe in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid session name")
    session_dir = os.path.join(HISTORY_DIR, "sessions", safe)
    if not os.path.isdir(session_dir):
        raise HTTPException(status_code=404, detail="session not found")
    return AttachmentRegistry(session_dir)


@router.get("/{name}/attachments")
async def list_attachments(name: str, response: Response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return {"attachments": _registry(name).list()}


@router.post("/{name}/attachments")
async def upload_attachment(name: str, request: Request, file: UploadFile = File(...)):
    registry = _registry(name)
    suffix = os.path.splitext(file.filename or "attachment")[1][:20]
    fd, temp_path = tempfile.mkstemp(prefix="mucli-upload-", suffix=suffix)
    size = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > registry.max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"attachment exceeds {registry.max_bytes} bytes",
                    )
                handle.write(chunk)
        descriptor = registry.add(
            name=file.filename or "attachment",
            source_path=temp_path,
            mime_type=file.content_type or "application/octet-stream",
        )
    except AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass

    await request.app.state.bus.publish({
        "kind": "attachment_created",
        "attachment": descriptor,
        "session_name": name,
    })
    return {"ok": True, "attachment": descriptor}


@router.get("/{name}/attachments/{attachment_id}/download")
async def download_attachment(name: str, attachment_id: str):
    registry = _registry(name)
    descriptor = registry.get(attachment_id)
    path = registry.resolve_path(attachment_id)
    if descriptor is None or path is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    return FileResponse(
        path,
        media_type=descriptor.get("mime_type") or "application/octet-stream",
        filename=descriptor.get("name") or "attachment",
    )


@router.delete("/{name}/attachments/{attachment_id}")
async def delete_attachment(name: str, attachment_id: str, request: Request):
    if request.app.state.session_busy_for(name).is_set():
        raise HTTPException(status_code=409, detail="cannot delete attachments during an active turn")
    if not _registry(name).remove(attachment_id):
        raise HTTPException(status_code=404, detail="attachment not found")
    await request.app.state.bus.publish({
        "kind": "attachment_deleted",
        "attachment_id": attachment_id,
        "session_name": name,
    })
    return {"ok": True, "attachment_id": attachment_id}
