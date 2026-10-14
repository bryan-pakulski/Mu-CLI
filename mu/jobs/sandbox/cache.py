"""Shared dependency cache for implement and checks containers.

One host directory per ``(repository, toolchain, lockfile hash)`` under
``$MUCLI_HOME/cache/deps/``, mounted read-write at ``/cache`` and reused by every job of that
repository whose lockfiles (at the job's BASE commit) are identical, so the second job does not
re-download the world. Package managers are pointed at ``/cache`` subdirectories by
:data:`mu.jobs.sandbox.toolchain.CACHE_ENV`.

* Plan and review never get it (they are read-only and build nothing).
* Keys use the repository's real path: two clones never share a cache, so one checkout's jobs
  cannot plant packages in another's.
* ``sandbox.cache_max_gb`` (default 20; 0 = no cap) bounds the total: least recently used
  entries are removed first, never one used within :data:`IN_USE_GRACE_S`.

Security note: a job's implement stage can write anything into its cache entry, and a later
job of the same repository and lockfiles reads it. That is the same trust boundary as the
repository itself (the next job runs that repository's code anyway), and checks still run in
the sandbox; remove ``$MUCLI_HOME/cache/deps`` to start clean.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

STAMP = ".mucli-last-used"
PRUNE_STAMP = ".mucli-pruned.json"
PRUNE_INTERVAL_S = 3600
IN_USE_GRACE_S = 24 * 3600
DEFAULT_MAX_GB = 20

# Lockfiles whose content identifies a dependency set (any toolchain).
LOCKFILES = frozenset({
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock",
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "requirements.txt", "Cargo.lock", "Gemfile.lock",
    "composer.lock", "go.sum", "mix.lock", "pubspec.lock", "packages.lock.json", "gradle.lockfile",
    "deno.lock", "Package.resolved", "flake.lock",
})


def cache_root() -> Path:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
    return Path(root) / "cache" / "deps"


def _slug(text: str, limit: int = 32) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text or "")).strip("-.")[:limit] or "x"


def repository_identity(job: Any) -> str:
    from .toolchain import job_repository
    repo = job_repository(job)
    if not repo:
        return ""
    real = os.path.realpath(os.path.expanduser(repo))
    return f"{_slug(os.path.basename(real), 24)}-{hashlib.sha1(real.encode()).hexdigest()[:12]}"


def _within(path: str, roots: Sequence[str]) -> bool:
    return not roots or any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def lockfile_hash(job: Any, paths: Sequence[str] = ()) -> str:
    """Hash of the lockfiles at the job's base commit (inside ``paths`` when the binding is
    limited to components). ``nolock`` when there are none or the base is unreadable."""
    from .repo_config import base_blob_ids, list_base_files
    if not str(getattr(job, "base_sha", "") or ""):
        return "nolock"
    files = [p for p in list_base_files(job) if os.path.basename(p) in LOCKFILES and _within(p, paths)]
    blobs = base_blob_ids(job, sorted(files))
    if not blobs:
        return "nolock"
    digest = hashlib.sha1("".join(f"{k}\0{v}\n" for k, v in sorted(blobs.items())).encode())
    return digest.hexdigest()[:16]


def max_bytes(settings: Optional[Dict[str, Any]] = None) -> int:
    from .cli import setting
    try:
        gb = float(setting("sandbox.cache_max_gb", DEFAULT_MAX_GB, settings))
    except (TypeError, ValueError):
        gb = DEFAULT_MAX_GB
    return max(0, int(gb * 1024 ** 3))


def entry_dir(repo_id: str, toolchain: str, lock_hash: str) -> Path:
    return cache_root() / _slug(repo_id, 64) / _slug(toolchain) / _slug(lock_hash)


def shared_cache_dir(job: Any, toolchain: str, paths: Sequence[str] = (), *,
                     settings: Optional[Dict[str, Any]] = None) -> str:
    """Create/reuse the cache entry for this job and toolchain; '' when the job has no repository."""
    repo_id = repository_identity(job)
    if not repo_id:
        return ""
    path = entry_dir(repo_id, toolchain or "runtime", lockfile_hash(job, paths))
    path.mkdir(parents=True, exist_ok=True)
    (path / STAMP).write_text(str(time.time()))
    try:
        prune(max_bytes(settings), keep=(path,))
    except OSError:
        pass  # pruning is housekeeping; never block a stage on it
    return str(path)


def _size(path: Path) -> int:
    total = 0
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in files + dirs:
            try:
                st = os.lstat(os.path.join(directory, name))
            except OSError:
                continue
            if not (st.st_mode & 0o170000 == 0o040000):
                total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
    return total


def _last_used(path: Path) -> float:
    try:
        return float((path / STAMP).read_text().strip())
    except (OSError, ValueError):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0


def entries(root: Optional[Path] = None) -> List[Path]:
    root = root or cache_root()
    if not root.is_dir():
        return []
    return [lock for repo in root.iterdir() if repo.is_dir() and not repo.is_symlink()
            for tool in repo.iterdir() if tool.is_dir() and not tool.is_symlink()
            for lock in tool.iterdir() if lock.is_dir() and not lock.is_symlink()]


def prune(limit: int, *, keep: Iterable[Path] = (), force: bool = False, now: Optional[float] = None,
          root: Optional[Path] = None) -> Dict[str, Any]:
    """LRU-prune entries until the total is at most ``limit`` bytes (0 = unlimited).

    Throttled to once per :data:`PRUNE_INTERVAL_S` unless ``force``. Entries in ``keep`` or used
    within :data:`IN_USE_GRACE_S` are never removed."""
    root = root or cache_root()
    now = time.time() if now is None else now
    stamp = root / PRUNE_STAMP
    report: Dict[str, Any] = {"pruned": [], "bytes": 0, "limit": limit, "at": now}
    if not root.is_dir():
        return report
    if not force:
        try:
            last = json.loads(stamp.read_text())
            if now - float(last.get("at") or 0) < PRUNE_INTERVAL_S:
                return {**last, "skipped": True}
        except (OSError, ValueError, AttributeError):
            pass
    kept = {str(Path(k).resolve()) for k in keep}
    rows = sorted(((_last_used(p), p, _size(p)) for p in entries(root)), key=lambda r: r[0])
    total = sum(r[2] for r in rows)
    if limit:
        for used, path, size in rows:
            if total <= limit:
                break
            if str(path.resolve()) in kept or now - used < IN_USE_GRACE_S:
                continue
            shutil.rmtree(path, ignore_errors=True)
            total -= size
            report["pruned"].append(str(path))
    report["bytes"] = total
    try:
        stamp.write_text(json.dumps(report))
    except OSError:
        pass
    return report


def usage() -> Dict[str, Any]:
    """Last measured size (from the prune stamp; no tree walk) for Doctor."""
    try:
        data = json.loads((cache_root() / PRUNE_STAMP).read_text())
        return {"bytes": int(data.get("bytes") or 0), "at": float(data.get("at") or 0)}
    except (OSError, ValueError, AttributeError):
        return {"bytes": 0, "at": 0.0}


def remove_job_targets(job_id: str, root: Optional[Path] = None) -> None:
    """A job's own build output (``target/<job12>``, see CACHE_ENV) leaves with the job."""
    safe = _slug(str(job_id)[:12])
    for entry in entries(root):
        shutil.rmtree(entry / "target" / safe, ignore_errors=True)
