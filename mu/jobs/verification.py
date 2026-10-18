"""Durable check-run evidence (``VerificationRun`` manifests) and shared check helpers."""

from __future__ import annotations

import json
import os
import sqlite3
import re
import subprocess
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from utils.config import HISTORY_DIR

from .store import JobStore


OUTPUT_LIMIT = 24000


def _bounded(text: str, limit: int = OUTPUT_LIMIT) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    half = max(1, (limit - 80) // 2)
    return value[:half] + "\n… [verification output truncated] …\n" + value[-half:]


@dataclass(frozen=True)
class VerificationCheck:
    command: str
    return_code: Optional[int]
    passed: bool
    timed_out: bool
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    # Honest gate (job_templates.md §5): passed | failed | unrunnable ('' = recorded before it).
    status: str = ""
    # For unrunnable checks: {tool, cause, hint}.
    unrunnable: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "VerificationCheck":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(raw or {}).items() if k in allowed})


@dataclass(frozen=True)
class VerificationRun:
    id: str
    job_id: str
    status: str
    passed: bool
    started_at: float
    finished_at: float
    duration_ms: int
    base_sha: str
    head_sha: str
    branch: str
    worktree: str
    checks: List[VerificationCheck] = field(default_factory=list)
    changed_files: List[Dict[str, Any]] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    diff_stat: str = ""
    dirty: bool = False
    dirty_status: str = ""
    manifest_path: str = ""
    summary: Dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        """False when any check could not run (the run proves nothing about those suites)."""
        if "verified" in (self.summary or {}):
            return bool(self.summary["verified"])
        return self.status != "unverified"

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["checks"] = [check.to_dict() for check in self.checks]
        value["verified"] = self.verified
        return value


class VerificationStore:
    def __init__(self, store: JobStore, *, evidence_root: Optional[str] = None):
        self.store = store
        self.evidence_root = os.path.abspath(
            os.path.expanduser(evidence_root or os.path.join(HISTORY_DIR, "jobs", "evidence"))
        )
        os.makedirs(self.evidence_root, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.store.path, timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS job_verifications (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    base_sha TEXT NOT NULL DEFAULT '',
                    head_sha TEXT NOT NULL DEFAULT '',
                    branch TEXT NOT NULL DEFAULT '',
                    worktree TEXT NOT NULL DEFAULT '',
                    checks_json TEXT NOT NULL DEFAULT '[]',
                    changed_files_json TEXT NOT NULL DEFAULT '[]',
                    additions INTEGER NOT NULL DEFAULT 0,
                    deletions INTEGER NOT NULL DEFAULT 0,
                    diff_stat TEXT NOT NULL DEFAULT '',
                    dirty INTEGER NOT NULL DEFAULT 0,
                    dirty_status TEXT NOT NULL DEFAULT '',
                    manifest_path TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS job_verifications_job_idx
                    ON job_verifications(job_id, finished_at DESC);
                """
            )
        finally:
            conn.close()

    def save(self, run: VerificationRun) -> VerificationRun:
        job_dir = os.path.join(self.evidence_root, run.job_id)
        os.makedirs(job_dir, exist_ok=True)
        manifest_path = run.manifest_path or os.path.join(job_dir, f"{run.id}.json")
        value = run.to_dict()
        value["manifest_path"] = manifest_path

        # DB row first, then the manifest: the row is the index of record;
        # a crash before the rename leaves only an orphan tmp file, never a
        # half-written manifest at its final path or a truncated replacement
        # of prior evidence.
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR REPLACE INTO job_verifications (
                    id, job_id, status, passed, started_at, finished_at, duration_ms,
                    base_sha, head_sha, branch, worktree, checks_json,
                    changed_files_json, additions, deletions, diff_stat, dirty,
                    dirty_status, manifest_path, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id, run.job_id, run.status, int(run.passed), run.started_at,
                    run.finished_at, run.duration_ms, run.base_sha, run.head_sha,
                    run.branch, run.worktree,
                    json.dumps([c.to_dict() for c in run.checks], ensure_ascii=False),
                    json.dumps(run.changed_files, ensure_ascii=False),
                    int(run.additions), int(run.deletions), run.diff_stat, int(run.dirty),
                    run.dirty_status, manifest_path,
                    json.dumps(run.summary, ensure_ascii=False, default=str),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

        # Atomically publish the manifest only after the row is durable:
        # tmp file in the same directory, fsync, rename.
        tmp_path = f"{manifest_path}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, manifest_path)
        return self.get(run.id)

    def get(self, verification_id: str) -> VerificationRun:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM job_verifications WHERE id = ?",
                (verification_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(verification_id)
        return self._from_row(row)

    def list(self, job_id: str, *, limit: int = 50) -> List[VerificationRun]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM job_verifications
                WHERE job_id = ? ORDER BY finished_at DESC LIMIT ?
                """,
                (job_id, max(1, min(int(limit), 500))),
            ).fetchall()
        finally:
            conn.close()
        return [self._from_row(row) for row in rows]

    def latest(self, job_id: str) -> Optional[VerificationRun]:
        values = self.list(job_id, limit=1)
        return values[0] if values else None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> VerificationRun:
        checks_raw = json.loads(row["checks_json"] or "[]")
        return VerificationRun(
            id=row["id"],
            job_id=row["job_id"],
            status=row["status"],
            passed=bool(row["passed"]),
            started_at=float(row["started_at"]),
            finished_at=float(row["finished_at"]),
            duration_ms=int(row["duration_ms"]),
            base_sha=row["base_sha"],
            head_sha=row["head_sha"],
            branch=row["branch"],
            worktree=row["worktree"],
            checks=[VerificationCheck.from_dict(item) for item in checks_raw],
            changed_files=json.loads(row["changed_files_json"] or "[]"),
            additions=int(row["additions"]),
            deletions=int(row["deletions"]),
            diff_stat=row["diff_stat"],
            dirty=bool(row["dirty"]),
            dirty_status=row["dirty_status"],
            manifest_path=row["manifest_path"],
            summary=json.loads(row["summary_json"] or "{}"),
        )


def _kill_process_group(pgid: int) -> None:
    """Best-effort SIGTERM→SIGKILL of an entire process group. Used after a
    validation-command timeout: subprocess kills only the shell, so without
    this its spawned descendants would survive and could keep mutating the
    worktree after verification inspects it."""
    try:
        import signal
        import time as _time

        os.killpg(pgid, signal.SIGTERM)
        _time.sleep(0.2)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Group already gone or restricted - nothing further to do.
        pass
    except AttributeError:
        # Non-POSIX platform without os.killpg: at minimum kill the shell
        # itself (pid == pgid here) so the caller's follow-up communicate()
        # cannot block forever. Callers also pass a bounded timeout to the
        # follow-up communicate() as a second net.
        try:
            os.kill(pgid, signal.SIGTERM)
            _time.sleep(0.2)
            os.kill(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def ensure_safe_job_id(job_id: str) -> str:
    """Reject job ids that could escape the worktree/evidence roots when
    joined into paths (absolute ids, '.'/'..' path components, separators).
    Harmless consecutive dots inside one component (e.g. release..v2) pass."""
    if not job_id or not _SAFE_JOB_ID.match(job_id):
        raise ValueError(
            f"unsafe job_id {job_id!r}: must match {_SAFE_JOB_ID.pattern}"
        )
    # The regex already bans '/' and '\', so the only traversal risk is a
    # '.'/'..' component: a dot-leading id or an id made entirely of dots.
    if job_id.startswith(".") or set(job_id) == {"."}:
        raise ValueError(
            f"unsafe job_id {job_id!r}: '.'/'..' path components are not allowed"
        )
    return job_id


_STREAM_BUF_SIZE = 1 << 16  # 64 KiB read chunks
_MAX_CAPTURE = 8 << 20  # 8 MiB hard cap per stream before truncation


class _CappedReader:
    """Drain a pipe on a thread so a command emitting unlimited output cannot exhaust worker
    memory. Output within ``cap`` is kept whole. Beyond it the first half of the budget (the
    head, where the first error usually is) and the last half (the tail) are kept, with a
    marker in between saying how much was left out."""

    def __init__(self, pipe, cap: int = _MAX_CAPTURE):
        self._pipe = pipe
        self._cap = cap
        self._head: list = []
        self._head_bytes = 0
        self._tail: deque = deque()
        self._tail_bytes = 0
        self._dropped = 0
        self._total = 0
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        half = self._cap // 2
        try:
            for line in iter(self._pipe.readline, ""):
                self._total += len(line)
                if self._head_bytes + len(line) <= half and not self._tail:
                    self._head.append(line)
                    self._head_bytes += len(line)
                    continue
                self._tail.append(line)
                self._tail_bytes += len(line)
                while self._tail_bytes > half and len(self._tail) > 1:
                    gone = self._tail.popleft()
                    self._tail_bytes -= len(gone)
                    self._dropped += len(gone)
        except Exception:
            pass
        finally:
            try:
                self._pipe.close()
            except Exception:
                pass

    def join(self, timeout: float = 5.0) -> tuple:
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        head, tail = "".join(self._head), "".join(self._tail)
        if not self._dropped:
            return head + tail, False
        marker = f"\n... [{self._total} bytes total, {self._dropped} bytes left out]\n"
        return head + marker + tail[-(self._cap // 2):], True


def _git(worktree: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", worktree, *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    return (result.stdout or "").strip()


def changed_files(worktree: str, base_sha: str, head_sha: str) -> tuple[List[Dict[str, Any]], int, int]:
    """Per-file name-status + numstat of ``base_sha..head_sha`` (evidence for a check run)."""
    if not base_sha or not head_sha:
        return [], 0, 0
    status_text = _git(worktree, "diff", "--name-status", f"{base_sha}..{head_sha}")
    numstat_text = _git(worktree, "diff", "--numstat", f"{base_sha}..{head_sha}")
    stats: Dict[str, tuple[Optional[int], Optional[int]]] = {}
    additions = 0
    deletions = 0
    for line in numstat_text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        add_raw, del_raw, path = parts
        add = int(add_raw) if add_raw.isdigit() else None
        delete = int(del_raw) if del_raw.isdigit() else None
        stats[path] = (add, delete)
        additions += add or 0
        deletions += delete or 0
    changed: List[Dict[str, Any]] = []
    for line in status_text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        change = parts[0]
        path = parts[-1]
        add, delete = stats.get(path, (None, None))
        changed.append({
            "status": change,
            "path": path,
            "additions": add,
            "deletions": delete,
        })
    return changed, additions, deletions
