"""Job evidence: screenshots, recordings and logs an agent captures while it tests its work.

A stage agent saves files under ``/output/evidence/`` in its container, optionally with
``/output/evidence/index.json`` (``[{"file": "home.png", "caption": "Home page after the
change"}]``). After the stage the host copies them into the job's artifacts
(``evidence/<stage>-<stamp>/``) and appends them to ``evidence/manifest.json``; the Checks tab
shows them, so a reviewer sees what the agent saw without running the code.

Evidence stays in the job's local folder: it is never committed or pushed. Only regular files
are copied (no symlinks), text evidence is secret-redacted on the way in, and the web serves
images and video inline but everything else (SVG and HTML included) as a download.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

MANIFEST = "evidence/manifest.json"
INDEX_NAMES = ("index.json", "evidence.json")

IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
VIDEO = {".mp4": "video/mp4", ".webm": "video/webm"}
TEXT = {".txt", ".log", ".md", ".json", ".csv", ".xml", ".html", ".htm", ".svg", ".yaml", ".yml", ".out"}

# Shown in every stage prompt that may capture evidence.
INSTRUCTIONS = (
    "Evidence: when you test or verify behaviour (a UI change, an API response, a CLI run), save what "
    "you observed to /output/evidence/ so the human reviewing this job can see it without running the "
    "code: screenshots (PNG/JPEG/WebP/GIF), short recordings (MP4/WebM) or text logs. A headless "
    "Chromium is usually available: `playwright screenshot --full-page <url> /output/evidence/<name>.png` "
    "(or Playwright from Python/Node; `playwright install chromium` if it is missing). Optionally add "
    "/output/evidence/index.json: "
    '[{"file": "home.png", "caption": "what it shows"}]. Never capture secrets or credentials.'
)


def kind_of(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE:
        return "image"
    if ext in VIDEO:
        return "video"
    if ext in TEXT:
        return "text"
    return "file"


def media_type(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return IMAGE.get(ext) or VIDEO.get(ext) or ("text/plain; charset=utf-8" if ext in TEXT else "application/octet-stream")


def _captions(source: Path) -> Dict[str, str]:
    for name in INDEX_NAMES:
        path = source / name
        if path.is_file() and not path.is_symlink():
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except ValueError:
                return {}
            rows = data.get("files") if isinstance(data, dict) else data
            out = {}
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get("file"):
                    out[str(row["file"]).lstrip("./")] = " ".join(str(row.get("caption") or "").split())[:500]
            return out
    return {}


def manifest(art) -> List[Dict[str, Any]]:
    value = art.read_json(MANIFEST, []) if art.exists() else []
    return value if isinstance(value, list) else []


def collect(art, outputs: Path, stage: str, *, attempt: Optional[int] = None) -> List[Dict[str, Any]]:
    """Copy ``outputs/evidence`` into the job's artifacts; returns the new manifest entries."""
    from mu.security.secret_paths import redact_secrets
    source = Path(outputs) / "evidence"
    if not source.is_dir() or source.is_symlink():
        return []
    captions = _captions(source)
    batch = f"{stage}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    entries: List[Dict[str, Any]] = []
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source).as_posix()
        if rel in INDEX_NAMES or path.is_symlink() or not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(source).parts):
            continue
        target = Path(art.path("evidence", batch, rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        kind = kind_of(rel)
        try:
            if kind == "text":
                text, _ = redact_secrets(path.read_text(encoding="utf-8", errors="replace"))
                target.write_text(text, encoding="utf-8")
            else:
                shutil.copyfile(path, target)
        except OSError:
            continue
        entries.append({"id": uuid.uuid4().hex[:12], "stage": stage, "attempt": attempt, "file": f"{batch}/{rel}",
                        "name": os.path.basename(rel), "caption": captions.get(rel, ""), "kind": kind,
                        "bytes": target.stat().st_size, "created_at": time.time()})
    if entries:
        art.write_json(MANIFEST, manifest(art) + entries)
    return entries


def collect_for_stage(service, job, outputs: Path, art, stage: str) -> List[Dict[str, Any]]:
    """:func:`collect`, then an ``evidence_added`` event (refreshes the Checks tab and notes)."""
    try:
        entries = collect(art, outputs, stage)
    except (OSError, ValueError):
        return []
    if entries:
        try:
            service.store.append_event(job.id, "evidence_added", reason=f"{len(entries)} evidence file(s) from {stage}",
                                       payload={"stage": stage, "count": len(entries),
                                                "files": [e["file"] for e in entries][:50],
                                                "captions": [e["caption"] for e in entries if e["caption"]][:10]})
        except Exception:  # noqa: BLE001 - the files are saved either way
            pass
    return entries


def resolve(art, file: str) -> Optional[Dict[str, Any]]:
    """The manifest entry for ``file`` (the only files the web serves), with its real path."""
    for entry in manifest(art):
        if entry.get("file") == file:
            try:
                path = art.path("evidence", *str(file).split("/"))
            except ValueError:
                return None
            if os.path.isfile(path) and not os.path.islink(path):
                return {**entry, "path": path}
    return None
