"""Node-local state: identity, verified blob cache, replay cache and check-mode history.

Layout under the state directory (``/var/lib/mucli-node`` by default)::

    identity.json        node id, credential, master URL, pinned cert, public keys (0600)
    master.pem           the master's pinned certificate
    blobs/<sha256>       content-addressed tool files, verified on write and on read
    replay.json          seen dispatch nonces until their deadline
    check_mode.json      "<tool>@<version>" keys that succeeded in check mode here
    work/                per-execution temp dirs
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from typing import Any, Dict, Optional

from .envelope import ReplayCache

_SHA = re.compile(r"^[0-9a-f]{64}$")


def _write_private(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class NodeStore:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self._lock = threading.Lock()
        for sub in ("", "blobs", "work"):
            os.makedirs(os.path.join(self.root, sub), mode=0o700, exist_ok=True)

    # ------------------------------------------------------------ identity
    @property
    def identity_path(self) -> str:
        return os.path.join(self.root, "identity.json")

    def load_identity(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.identity_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, ValueError):
            return None

    def save_identity(self, identity: Dict[str, Any]) -> None:
        _write_private(self.identity_path, json.dumps(identity, indent=2, sort_keys=True).encode())

    # ------------------------------------------------------------ blobs
    def blob_path(self, sha: str) -> str:
        if not _SHA.match(str(sha or "")):
            raise ValueError("invalid blob digest")
        return os.path.join(self.root, "blobs", sha)

    def has_blob(self, sha: str) -> bool:
        try:
            return self.read_blob(sha) is not None
        except ValueError:
            return False

    def put_blob(self, sha: str, data: bytes) -> None:
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError("blob content does not match its digest")
        _write_private(self.blob_path(sha), data)

    def read_blob(self, sha: str) -> Optional[bytes]:
        """The blob, re-verified against its digest (a tampered cache entry reads as missing)."""
        path = self.blob_path(sha)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            return None
        if hashlib.sha256(data).hexdigest() != sha:
            try:
                os.unlink(path)
            except OSError:
                pass
            return None
        return data

    # ------------------------------------------------------------ replay cache
    def _json(self, name: str, default):
        try:
            with open(os.path.join(self.root, name), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, ValueError):
            return default

    def replay_cache(self) -> ReplayCache:
        cache = ReplayCache(self._json("replay.json", {}))
        cache.prune(time.time())
        return cache

    def save_replay_cache(self, cache: ReplayCache) -> None:
        cache.prune(time.time())
        _write_private(os.path.join(self.root, "replay.json"), json.dumps(cache.entries).encode())

    # ------------------------------------------------------------ check-mode history
    def check_mode_passed(self) -> set:
        return set(self._json("check_mode.json", []))

    def record_check_mode_pass(self, key: str) -> None:
        with self._lock:
            passed = self.check_mode_passed()
            passed.add(key)
            _write_private(os.path.join(self.root, "check_mode.json"),
                           json.dumps(sorted(passed)).encode())

    def work_dir(self, exec_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", exec_id)[:64]
        return tempfile.mkdtemp(prefix=f"{safe}-", dir=os.path.join(self.root, "work"))
