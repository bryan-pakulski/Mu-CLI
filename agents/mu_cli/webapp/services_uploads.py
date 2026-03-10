from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


@dataclass(slots=True)
class UploadServiceDeps:
    persist: Callable[[Any], None]
    remove_uploaded_entry: Callable[[Any, str], bool]


@dataclass(slots=True)
class UploadService:
    deps: UploadServiceDeps

    def upload_files(self, runtime: Any, files: list[FileStorage]) -> list[dict[str, Any]]:
        session_dir = runtime.uploads_dir / runtime.session_name
        session_dir.mkdir(parents=True, exist_ok=True)
        uploaded: list[dict[str, Any]] = []

        for file in files:
            filename = secure_filename(file.filename or "upload.bin")
            if not filename:
                continue
            target = session_dir / filename
            file.save(target)

            raw = target.read_bytes()
            kind = self._detect_kind(target, raw)
            item = {
                "name": filename,
                "path": str(target),
                "size": len(raw),
                "kind": kind,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
            runtime.uploads.append(item)
            uploaded.append(item)

        self.deps.persist(runtime)
        return uploaded

    def clear_uploads(self, runtime: Any) -> int:
        session_dir = runtime.uploads_dir / runtime.session_name
        removed = 0
        if session_dir.exists():
            for item in session_dir.iterdir():
                if item.is_file():
                    item.unlink()
                    removed += 1
        runtime.uploads = []
        self.deps.persist(runtime)
        return removed

    def delete_upload(self, runtime: Any, safe_name: str) -> tuple[bool, str | None]:
        session_dir = runtime.uploads_dir / runtime.session_name
        target = session_dir / safe_name
        if not target.exists() or not target.is_file():
            return False, None

        target.unlink()
        self.deps.remove_uploaded_entry(runtime, safe_name)
        self.deps.persist(runtime)
        return True, safe_name

    def _detect_kind(self, target: Path, raw: bytes) -> str:
        kind = "binary"
        try:
            raw.decode("utf-8")
            kind = "text"
        except UnicodeDecodeError:
            if target.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                kind = "image"
        return kind
