"""Backup and restore (plan §10, P5): store, keys and blobs in one archive.

The database is copied with SQLite's online backup API, so a backup taken while the master
runs is consistent. The archive contains private keys and the secret store's data key: keep
it as carefully as the master itself (it is written 0600).
"""

from __future__ import annotations

import json
import os
import sqlite3
import tarfile
import tempfile
import time
from typing import Any, Dict

from .store import orchestration_home


def backup(orch, path: str) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = os.path.join(tmp, "orch.sqlite3")
        src = sqlite3.connect(orch.store.path)
        dst = sqlite3.connect(db_copy)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
            tar.add(db_copy, arcname="orch.sqlite3")
            tar.add(os.path.join(orch.root, "keys"), arcname="keys")
            tar.add(orch.store.blob_dir, arcname="blobs")
            meta = json.dumps({"created_at": time.time(), "format": "mucli-orch-backup", "version": 1}).encode()
            info = tarfile.TarInfo("backup.json")
            info.size = len(meta)
            import io
            tar.addfile(info, io.BytesIO(meta))
    orch.store.audit("operator", "backup.create", os.path.basename(path), {})
    return {"path": path, "bytes": os.path.getsize(path)}


def restore(path: str, root: str = "") -> Dict[str, Any]:
    root = os.path.abspath(os.path.expanduser(root or orchestration_home()))
    if os.path.exists(os.path.join(root, "orch.sqlite3")):
        raise ValueError(f"{root} already has an orchestration store; restore into an empty home")
    os.makedirs(root, mode=0o700, exist_ok=True)
    with tarfile.open(path, mode="r:gz") as tar:
        names = tar.getnames()
        if "backup.json" not in names or "orch.sqlite3" not in names:
            raise ValueError("not a mucli orchestration backup")
        for member in tar.getmembers():
            name = os.path.normpath(member.name)
            if name.startswith(("/", "..")) or member.issym() or member.islnk():
                raise ValueError(f"unsafe entry in backup: {member.name}")
        tar.extractall(root)
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "keys")):
        for name in filenames:
            os.chmod(os.path.join(dirpath, name), 0o600)
    return {"restored_to": root, "files": len(names)}
