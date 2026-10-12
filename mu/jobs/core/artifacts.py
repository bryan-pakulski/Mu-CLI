"""Per-job artifact folder (guide §1.1, Phase 1).

Stages hand off only through these files; ``job.metadata`` carries pointers.
Layout under ``$MUCLI_HOME/jobs/<id>/``::

    ticket.json  plan.md  plans/plan.v<N>.md  plans/draft.md
    diff.patch   review.json  checks/run-<n>/  costs.jsonl  logs/

All writes are atomic (tmp file + ``os.replace``).
"""

from __future__ import annotations

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


def artifacts_for(job: Any, root: Optional[str] = None) -> JobArtifacts:
    return JobArtifacts(getattr(job, "id", job), root=root)


def backfill(job: Any, root: Optional[str] = None) -> JobArtifacts:
    """Lazy migration (guide §10): write artifacts from legacy metadata once.

    Never overwrites existing files and never mutates ``job.metadata``.
    """
    art = artifacts_for(job, root).ensure()
    meta = dict(getattr(job, "metadata", None) or {})
    if not os.path.exists(art.ticket):
        ticket = dict(meta.get("jira") or {})
        ticket.setdefault("title", getattr(job, "title", ""))
        ticket.setdefault("description", getattr(job, "description", ""))
        ticket.setdefault("acceptance_criteria", list(getattr(job, "acceptance_criteria", []) or []))
        art.write_json("ticket.json", ticket)
    plan = meta.get("plan")
    if plan and not os.path.exists(art.plan):
        from .plan_doc import PlanDoc

        doc = PlanDoc.from_legacy(plan, job)
        art.write_text("plan.md", doc.render())
        art.write_text(os.path.join("plans", f"plan.v{doc.version}.md"), doc.render())
    rounds = list(meta.get("review_rounds") or [])
    if rounds and not os.path.exists(art.review):
        from .review_report import ReviewReport

        report = ReviewReport.from_legacy(rounds[-1], job_id=art.job_id)
        art.write_json("review.json", report.to_dict())
    return art


def migrate_metadata(service, job):
    """Archive legacy embedded handoffs before replacing them with file pointers."""
    meta = dict(job.metadata or {})
    keys = ("plan", "review_rounds", "plan_contract")
    if not any(k in meta for k in keys):
        return job
    art = backfill(job)
    if not art.read_json("legacy-handoffs.json"):
        art.write_json("legacy-handoffs.json", {k: meta[k] for k in keys if k in meta})
    for index, report in enumerate(meta.get("review_rounds") or [], 1):
        name = f"logs/review.round{index}.json"
        if art.read_json(name) is None:
            art.write_json(name, report)
    if meta.get("plan_contract") and not art.read_json("plans/contract.json"):
        art.write_json("plans/contract.json", meta["plan_contract"])
    for key in keys:
        meta.pop(key, None)
    meta["artifacts"] = art.pointers()
    # A worker may have added runtime metadata while files were being written.
    # Replace only the snapshot we migrated; a later open can retry otherwise.
    from ..store import _json
    with service.store._transaction() as conn:
        conn.execute("UPDATE jobs SET metadata_json=?, version=version+1 WHERE id=? AND metadata_json=?",
                     (_json(meta), job.id, _json(job.metadata or {})))
    return service.get(job.id)
