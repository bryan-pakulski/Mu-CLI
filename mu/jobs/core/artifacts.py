"""Per-job artifact folder (guide §1.1, Phase 1).

Stages hand off only through these files; ``job.metadata`` carries pointers.
Layout under ``$MUCLI_HOME/jobs/<id>/``::

    ticket.json  plan.md  plans/plan.v<N>.md  plans/draft.md
    diff.patch   review.json  checks/run-<n>/  costs.jsonl  logs/

All writes are atomic (tmp file + ``os.replace``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from typing import Any, Dict, Iterator, List, Optional

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def jobs_root() -> str:
    from utils.config import HISTORY_DIR

    return os.path.join(os.path.expanduser(os.getenv("MUCLI_HOME", HISTORY_DIR)), "jobs")


def _atomic_write(path: str, data: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class JobArtifacts:
    def __init__(self, job_id: str, root: Optional[str] = None):
        job_id = str(job_id or "")
        if not _SAFE_ID.match(job_id) or job_id in {".", ".."}:
            raise ValueError(f"unsafe job id {job_id!r}")
        self.job_id = job_id
        self.dir = os.path.join(root or jobs_root(), job_id)

    # ---------------------------------------------------------------- paths
    def path(self, *parts: str) -> str:
        full = os.path.realpath(os.path.join(self.dir, *parts))
        base = os.path.realpath(self.dir)
        if full != base and not full.startswith(base + os.sep):
            raise ValueError("artifact path escapes job folder")
        return full

    @property
    def ticket(self) -> str: return self.path("ticket.json")
    @property
    def plan(self) -> str: return self.path("plan.md")
    @property
    def plans_dir(self) -> str: return self.path("plans")
    @property
    def draft(self) -> str: return self.path("plans", "draft.md")
    @property
    def diff(self) -> str: return self.path("diff.patch")
    @property
    def review(self) -> str: return self.path("review.json")
    @property
    def checks_dir(self) -> str: return self.path("checks")
    @property
    def costs(self) -> str: return self.path("costs.jsonl")
    @property
    def logs_dir(self) -> str: return self.path("logs")

    def exists(self) -> bool:
        return os.path.isdir(self.dir)

    def ensure(self) -> "JobArtifacts":
        for sub in ("plans", "checks", "logs"):
            os.makedirs(os.path.join(self.dir, sub), exist_ok=True)
        return self

    # ------------------------------------------------------------------ IO
    def write_text(self, rel: str, text: str) -> str:
        target = self.path(rel)
        _atomic_write(target, str(text))
        return target

    def read_text(self, rel: str) -> Optional[str]:
        try:
            with open(self.path(rel), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return None

    def write_json(self, rel: str, value: Any) -> str:
        return self.write_text(rel, json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")

    def read_json(self, rel: str, fallback: Any = None) -> Any:
        text = self.read_text(rel)
        if text is None:
            return fallback
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return fallback

    def append_cost(self, record: Dict[str, Any]) -> None:
        os.makedirs(self.dir, exist_ok=True)
        with open(self.costs, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def iter_costs(self) -> Iterator[Dict[str, Any]]:
        try:
            with open(self.costs, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            return

    def stage_costs(self) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for rec in self.iter_costs():
            stage = str(rec.get("stage") or "")
            totals[stage] = totals.get(stage, 0.0) + float(rec.get("cost_usd") or 0.0)
        return totals

    def stage_tokens(self) -> Dict[str, int]:
        """Tokens each stage charged against its token budget (``charged_tokens`` per record)."""
        totals: Dict[str, int] = {}
        for rec in self.iter_costs():
            try:
                charged = int(rec.get("charged_tokens") or 0)
            except (TypeError, ValueError):
                continue
            stage = str(rec.get("stage") or "")
            totals[stage] = totals.get(stage, 0) + max(0, charged)
        return totals

    # --------------------------------------------------------------- checks
    def check_runs(self) -> List[int]:
        try:
            names = os.listdir(self.checks_dir)
        except FileNotFoundError:
            return []
        runs = []
        for name in names:
            if name.startswith("run-") and name[4:].isdigit():
                runs.append(int(name[4:]))
        return sorted(runs)

    def next_check_run(self) -> int:
        runs = self.check_runs()
        n = (runs[-1] + 1) if runs else 1
        os.makedirs(self.path("checks", f"run-{n}"), exist_ok=True)
        return n

    # --------------------------------------------------------- viewed marks
    def diff_hashes(self) -> Dict[str, str]:
        return diff_file_hashes(self.read_text("diff.patch") or "")

    def viewed(self) -> List[str]:
        """Paths marked viewed whose diff block is unchanged since marking."""
        marks = self.read_json("viewed.json", {}) or {}
        if not isinstance(marks, dict):
            return []
        current = self.diff_hashes()
        return sorted(p for p, h in marks.items() if p in current and str(h or "") in ("", current[p]))

    def set_viewed(self, path: str, viewed: bool) -> List[str]:
        marks = self.read_json("viewed.json", {}) or {}
        if not isinstance(marks, dict):
            marks = {}
        if viewed:
            marks[path] = self.diff_hashes().get(path, "")
        else:
            marks.pop(path, None)
        self.write_json("viewed.json", marks)
        return self.viewed()

    # ------------------------------------------------------------ pointers
    def pointers(self) -> Dict[str, str]:
        """Metadata-safe pointers (relative names only)."""
        out = {"dir": self.dir}
        for key, rel in (("ticket", "ticket.json"), ("plan", "plan.md"), ("diff", "diff.patch"),
                         ("review", "review.json")):
            if os.path.exists(self.path(rel)):
                out[key] = rel
        return out

    def remove(self) -> bool:
        if not os.path.isdir(self.dir):
            return False
        if os.path.lexists(os.path.join(self.dir, "worktree")):
            raise OSError("job worktree remains; remove it through the guarded worktree manager before purging artifacts")
        shutil.rmtree(self.dir)
        return True


def diff_file_hashes(patch: str) -> Dict[str, str]:
    """``{path: sha1(file diff block)}`` for a unified git diff.

    The block is everything from a ``diff --git`` header to the next one, so
    any hunk change for a file changes its hash (and clears its viewed mark).
    """
    blocks: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in str(patch or "").splitlines(keepends=True):
        if line.startswith("diff --git "):
            current = _diff_path(line)
            blocks.setdefault(current, [])
        if current is not None:
            blocks[current].append(line)
        elif line.startswith("+++ ") or line.startswith("--- "):
            # Plain unified diff without git headers.
            name = line[4:].strip().split("\t")[0]
            if name != "/dev/null":
                current = name[2:] if name[:2] in ("a/", "b/") else name
                blocks.setdefault(current, []).append(line)
    return {path: hashlib.sha1("".join(lines).encode("utf-8", "surrogateescape")).hexdigest()
            for path, lines in blocks.items()}


def _diff_path(header: str) -> str:
    rest = header[len("diff --git "):].rstrip("\n")
    marker = rest.rfind(" b/")
    if marker >= 0:
        return rest[marker + 3:]
    parts = rest.split(" ")
    last = parts[-1] if parts else rest
    return last[2:] if last.startswith("b/") else last


def artifacts_for(job: Any, root: Optional[str] = None) -> JobArtifacts:
    return JobArtifacts(getattr(job, "id", job), root=root)


def ensure_ticket(job: Any, root: Optional[str] = None) -> JobArtifacts:
    """The job's artifact folder with ``ticket.json`` written once from the job (and any linked
    ticket in its metadata). Never overwrites an existing ticket."""
    art = artifacts_for(job, root).ensure()
    if not os.path.exists(art.ticket):
        meta = dict(getattr(job, "metadata", None) or {})
        ticket = {**dict(meta.get("jira") or {}), **dict(meta.get("ticket") or {})}
        ticket.setdefault("title", getattr(job, "title", ""))
        ticket.setdefault("description", getattr(job, "description", ""))
        ticket.setdefault("acceptance_criteria", list(getattr(job, "acceptance_criteria", []) or []))
        art.write_json("ticket.json", ticket)
    return art


# ------------------------------------------------------------------ live diff
LIVE_DIFF_MAX_BYTES = 400_000
LIVE_DIFF_MAX_UNTRACKED = 500
_LIVE_CACHE: Dict[str, tuple] = {}
_LIVE_CACHE_TTL = 3.0


def _live_git(worktree: str, args: List[str], *, env: Dict[str, str], timeout: float) -> Optional[str]:
    import subprocess

    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-C", worktree, *args]
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL, check=False, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def live_diff(job: Any, *, max_bytes: int = LIVE_DIFF_MAX_BYTES, timeout: float = 20.0,
              use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """Current worktree changes against the job's base commit (read-only).

    ``{"patch", "files", "untracked", "truncated", "base_sha", "generated_at", "bytes"}``,
    or ``None`` when the job has no host worktree or base commit. The real index is
    never touched: a throw-away ``GIT_INDEX_FILE`` is seeded from the base tree and
    new files are recorded intent-to-add (no blobs written), so ``git diff <base>``
    shows modified, deleted and untracked (non-ignored) files. Output is bounded,
    secret-scrubbed and cached for a few seconds per job.
    """
    import time

    worktree = str(getattr(job, "worktree", "") or "")
    base = str(getattr(job, "base_sha", "") or "")
    if not worktree or not base or not re.fullmatch(r"[0-9a-fA-F]{7,64}", base) or not os.path.isdir(worktree):
        return None
    key = f"{getattr(job, 'id', '')}:{worktree}:{base}:{int(max_bytes)}"
    now = time.monotonic()
    cached = _LIVE_CACHE.get(key)
    if use_cache and cached and now - cached[0] < _LIVE_CACHE_TTL:
        return dict(cached[1])
    fd, index = tempfile.mkstemp(prefix="mucli-live-index-")
    os.close(fd)
    os.unlink(index)  # git creates it; an empty file is not a valid index
    env = {**os.environ, "GIT_INDEX_FILE": index, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_EXTERNAL_DIFF"):
        env.pop(var, None)
    try:
        if _live_git(worktree, ["read-tree", base], env=env, timeout=timeout) is None:
            return None
        listing = _live_git(worktree, ["ls-files", "-z", "--others", "--exclude-standard"], env=env,
                            timeout=timeout) or ""
        untracked = sorted(p for p in listing.split("\0") if p)
        if untracked:
            # Intent-to-add only (no blob writes) so new files appear in ``git diff``.
            batch = untracked[:LIVE_DIFF_MAX_UNTRACKED]
            _live_git(worktree, ["add", "--intent-to-add", "--", *batch], env=env, timeout=timeout)
        names = _live_git(worktree, ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--name-only", base],
                          env=env, timeout=timeout) or ""
        patch = _live_git(worktree, ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--find-renames",
                                     base, "--"], env=env, timeout=timeout)
        if patch is None:
            return None
    finally:
        for path in (index, index + ".lock"):
            try:
                os.unlink(path)
            except OSError:
                pass
    from mu.security.secret_paths import redact_secrets

    limit = max(10_000, int(max_bytes))
    size = len(patch.encode("utf-8", "replace"))
    truncated = size > limit
    if truncated:
        patch = patch[:limit]
        cut = patch.rfind("\ndiff --git ")
        if cut > 0:
            patch = patch[:cut + 1]
        patch += "\n... [live diff truncated] ...\n"
    patch, _ = redact_secrets(patch)
    out = {"patch": patch, "files": [n for n in names.splitlines() if n.strip()],
           "untracked": untracked[:LIVE_DIFF_MAX_UNTRACKED], "untracked_total": len(untracked),
           "truncated": truncated, "base_sha": base, "generated_at": time.time(), "bytes": size}
    _LIVE_CACHE[key] = (now, out)
    if len(_LIVE_CACHE) > 64:
        for stale in sorted(_LIVE_CACHE, key=lambda k: _LIVE_CACHE[k][0])[:32]:
            _LIVE_CACHE.pop(stale, None)
    return dict(out)
