"""Jobs on a folder that is not a git repository: run them on a managed git snapshot.

The job pipeline needs git (worktrees, base commits, diffs, checks). A plain folder is
mirrored into ``$MUCLI_HOME/repos/folders/<name>-<sha8(realpath)>``, a private repository on
branch ``main``, and committed; the job runs on that snapshot like any repository. Each new
job on the same folder refreshes the snapshot first (a new commit only when the folder
changed), so the job starts from the folder as it is now. The job's change comes back as a
patch (``GET /api/jobs/<id>/patch``) to apply in the folder with ``git apply``; nothing is
written to the folder and nothing is pushed.

Left out of the snapshot, with the reason recorded:

* ``.git`` directories and dependency/cache trees (:data:`SKIP_DIRS`; jobs install their own);
* secret files, by the same rule the sandbox pre-check enforces (``is_denied_path``, env
  templates and test fixtures excepted) — the job would otherwise fail closed;
* symlinks that are absolute or resolve outside the folder;
* anything the folder's own ``.gitignore`` files ignore (it stays untracked in the snapshot);
* mucli's own state (``$MUCLI_HOME``, which holds the snapshot itself) when it lies inside the folder.

The snapshot may hold at most ``jobs.folder_snapshot_max_mb`` (Settings) of files.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
                       ".tox", ".gradle"})
BRANCH = "main"
_SOURCE_KEY = "mucli.snapshotsource"


class SnapshotError(ValueError):
    """The folder could not be snapshotted (shown to the user as a 400)."""


@dataclass
class SnapshotResult:
    path: str
    source: str
    base_sha: str
    created: bool
    changed: bool
    files: int = 0
    bytes: int = 0
    skipped: List[str] = field(default_factory=list)

    def metadata(self) -> dict:
        return {"source_path": self.source, "snapshot_path": self.path, "base_sha": self.base_sha,
                "files": self.files, "bytes": self.bytes, "skipped": self.skipped[:200],
                "skipped_total": len(self.skipped)}


def snapshot_root() -> str:
    from .clone import repos_root
    return os.path.join(repos_root(), "folders")


def snapshot_path(folder: str, *, root: Optional[str] = None) -> str:
    real = os.path.realpath(os.path.expanduser(folder))
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(real)).strip("-.") or "folder"
    digest = hashlib.sha256(real.encode("utf-8")).hexdigest()[:8]
    return os.path.join(root or snapshot_root(), f"{name}-{digest}")


def max_bytes() -> int:
    from .job_settings import rule_or
    try:
        return int(float(rule_or("jobs.folder_snapshot_max_mb", 2048)) * 1024 * 1024)
    except (TypeError, ValueError):
        return 2048 * 1024 * 1024


def _git(path: str, *args: str, check: bool = True) -> str:
    from .repository import git_timeout_seconds
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="mucli snapshot", GIT_COMMITTER_NAME="mucli snapshot",
               GIT_AUTHOR_EMAIL="snapshot@mucli.invalid", GIT_COMMITTER_EMAIL="snapshot@mucli.invalid")
    proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-C", path, *args],
                          text=True, capture_output=True, env=env, timeout=max(60, git_timeout_seconds() * 5),
                          stdin=subprocess.DEVNULL, check=False)
    if check and proc.returncode != 0:
        raise SnapshotError(f"git {args[0]} failed in the folder snapshot: {(proc.stderr or proc.stdout).strip()[-500:]}")
    return proc.stdout


def _secret(path: str, relative: str, name: str) -> bool:
    from mu.security.secret_paths import is_denied_path, is_test_fixture
    from .sandbox.policy import is_env_template
    return bool(is_denied_path(path)[0]) and not is_env_template(name) and not is_test_fixture(relative)


def _state_home() -> str:
    from utils.config import HISTORY_DIR
    return os.path.realpath(os.path.expanduser(os.getenv("MUCLI_HOME", HISTORY_DIR)))


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _mirror(source: str, target: str, limit: int) -> tuple[int, int, List[str]]:
    """Replace ``target``'s files (``.git`` kept) with the filtered contents of ``source``."""
    # Never copy the snapshot into itself or mucli's state (sessions, jobs, credentials) into a job.
    protected = [os.path.realpath(target), _state_home()]
    for entry in os.listdir(target):
        if entry == ".git":
            continue
        path = os.path.join(target, entry)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    real_root = os.path.realpath(source)
    files = total = 0
    skipped: List[str] = []
    for directory, dirs, names in os.walk(source, followlinks=False):
        rel_dir = os.path.relpath(directory, source)
        rel = (lambda n: n) if rel_dir == "." else (lambda n, d=rel_dir: os.path.join(d, n))
        kept_dirs = []
        for d in dirs:
            path = os.path.join(directory, d)
            if os.path.islink(path):
                names.append(d)  # handled as a symlink below
            elif d in SKIP_DIRS:
                skipped.append(f"{rel(d)}/ (dependency or VCS directory)")
            elif any(_inside(p, os.path.realpath(path)) for p in protected):
                skipped.append(f"{rel(d)}/ (mucli state)")
            elif _secret(path, rel(d), d):
                skipped.append(f"{rel(d)}/ (secret path)")
            else:
                kept_dirs.append(d)
                os.makedirs(os.path.join(target, rel(d)), exist_ok=True)
        dirs[:] = kept_dirs
        for name in names:
            path, relative = os.path.join(directory, name), rel(name)
            dest = os.path.join(target, relative)
            if os.path.islink(path):
                link = os.readlink(path)
                resolved = os.path.realpath(path)
                if os.path.isabs(link) or not (resolved == real_root or resolved.startswith(real_root + os.sep)):
                    skipped.append(f"{relative} (symlink outside the folder)")
                    continue
                os.symlink(link, dest)
                continue
            if not os.path.isfile(path):
                continue  # sockets, fifos, devices
            if _secret(path, relative, name):
                skipped.append(f"{relative} (secret path)")
                continue
            size = os.path.getsize(path)
            if total + size > limit:
                raise SnapshotError(
                    f"the folder holds more than {limit // (1024 * 1024)} MB of files; raise Settings → "
                    "Git & PR hosts → Folder snapshot limit, or add a .gitignore for large files")
            shutil.copy2(path, dest)
            files += 1
            total += size
    return files, total, skipped


def ensure_snapshot(folder: str, *, root: Optional[str] = None) -> SnapshotResult:
    """Create or refresh the snapshot of ``folder``; the result's ``base_sha`` is its current state."""
    source = os.path.realpath(os.path.expanduser(str(folder or "")))
    if not os.path.isdir(source):
        raise SnapshotError(f"folder {source} does not exist")
    if _inside(source, _state_home()):
        raise SnapshotError(f"folder {source} is inside mucli's state directory; choose a folder outside it")
    path = snapshot_path(source, root=root)
    from .clone import _lock
    with _lock(path):
        created = not os.path.isdir(os.path.join(path, ".git"))
        if created:
            os.makedirs(path, exist_ok=True)
            _git(path, "init", "-q", "-b", BRANCH)
            _git(path, "config", _SOURCE_KEY, source)
        else:
            recorded = _git(path, "config", "--get", _SOURCE_KEY, check=False).strip()
            if recorded != source:
                raise SnapshotError(f"refusing to use {path}: it is not the snapshot of {source} (move or delete it)")
        files, total, skipped = _mirror(source, path, max_bytes())
        _git(path, "add", "-A")
        has_head = bool(_git(path, "rev-parse", "--verify", "--quiet", "HEAD", check=False).strip())
        changed = not has_head or bool(_git(path, "status", "--porcelain").strip())
        if changed:
            _git(path, "commit", "-q", "--allow-empty", "-m", f"Snapshot of {source}")
        base_sha = _git(path, "rev-parse", "HEAD").strip()
    return SnapshotResult(path=path, source=source, base_sha=base_sha, created=created, changed=changed,
                          files=files, bytes=total, skipped=skipped)


def is_snapshot_job(job) -> bool:
    return bool((getattr(job, "metadata", None) or {}).get("folder_snapshot"))
