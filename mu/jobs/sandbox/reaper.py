"""Remove a job's orphaned stage containers before its work is re-dispatched.

When a worker's lease expires (the worker process died, the host restarted, the
controller was killed) the stage container it started can keep running: a plan,
implement, review or checks container still writing into the job's worktree or
burning provider budget while a replacement worker starts the same stage. Lease
recovery calls :func:`reap_stage_containers` first so exactly one stage
container ever works on a job.

The job's engine container (``io.mucli.role=job-engine``, the per-job dockerd)
is kept: it holds the job's image cache and is reused by the next stage. Its
inner containers die with the stage that drove them.

Docker missing, the daemon down, or any docker error is a no-op (debug log):
recovery must never fail because cleanup could not run.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Callable, List, Optional, Sequence

logger = logging.getLogger("mucli")

LABEL_JOB = "io.mucli.job"
LABEL_ROLE = "io.mucli.role"
ENGINE_ROLE = "job-engine"

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _default_runner(timeout: float) -> Optional[Runner]:
    from .cli import container_cli
    binary = shutil.which(container_cli())
    if not binary:
        return None

    def run(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
        return subprocess.run([binary, *args], text=True, capture_output=True, timeout=timeout, check=False)

    return run


def _engine_name(job_id: str) -> str:
    return f"mucli-job-{str(job_id)[:12]}-engine"


def _list(run: Runner, selector: str) -> List[tuple]:
    fmt = "{{.ID}}\t{{.Names}}\t{{.Label \"" + LABEL_ROLE + "\"}}"
    result = run(["ps", "-a", "--filter", selector, "--format", fmt])
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "").strip()[:200] or f"docker ps exited {result.returncode}")
    rows = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if not parts or not parts[0].strip():
            continue
        parts += [""] * (3 - len(parts))
        rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return rows


def reap_stage_containers(job_id: str, *, runner: Optional[Runner] = None, timeout: float = 30.0) -> int:
    """Force-remove every non-engine container of ``job_id``; returns how many were removed."""
    job_id = str(job_id or "").strip()
    if not job_id:
        return 0
    run = runner or _default_runner(timeout)
    if run is None:
        logger.debug("reap_stage_containers(%s): docker not installed; nothing to reap", job_id)
        return 0
    try:
        rows = _list(run, f"label={LABEL_JOB}={job_id}")
        # Stage containers started without a job policy still carry the job's name prefix.
        rows += _list(run, f"name=^mucli-job-{job_id[:12]}-")
    except Exception as exc:  # noqa: BLE001 - docker down/hung: recovery proceeds without cleanup
        logger.debug("reap_stage_containers(%s): docker ps failed: %s", job_id, exc)
        return 0
    engine = _engine_name(job_id)
    removed = 0
    seen = set()
    for cid, name, role in rows:
        if cid in seen:
            continue
        seen.add(cid)
        if role == ENGINE_ROLE or name.lstrip("/") == engine:
            continue
        try:
            result = run(["rm", "-f", cid])
        except Exception as exc:  # noqa: BLE001
            logger.debug("reap_stage_containers(%s): rm %s failed: %s", job_id, cid, exc)
            continue
        if result.returncode == 0:
            removed += 1
        else:
            logger.debug("reap_stage_containers(%s): rm %s failed: %s", job_id, cid, (result.stderr or "").strip()[:200])
    if removed:
        logger.info("Job %s: removed %d orphaned stage container(s) before recovery", job_id[:10], removed)
    return removed
