"""Durable job v2 PUBLISHING stage: push the review branch, open a draft PR.

Runs after the reviewer approves a git-backed job. Provider is chosen from
``execution.publish_mode``:

* ``none``   - stage skipped (job goes straight to READY_FOR_REVIEW)
* ``auto``   - ``gh`` CLI if installed, else REST by ``origin`` host
* ``gh``     - GitHub CLI
* ``github`` - GitHub REST (``GITHUB_TOKEN`` / ``GH_TOKEN``)
* ``gitlab`` - GitLab REST merge request (``GITLAB_TOKEN``)

PRs are always opened as drafts. Tokens come from the environment only and
are never written to the job record. Any failure parks the job on a human
with ``PUBLISH_FAILED``; the human READY_FOR_REVIEW gate is kept on success.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import threading
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote

from .models import AttentionReason, Job, JobStatus

RunFn = Callable[[list, str], Tuple[int, str, str]]
PostFn = Callable[[str, Dict[str, str], Dict[str, Any]], Dict[str, Any]]

_SCP_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+?)(?:\.git)?/?$")
_URL_RE = re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+?)(?:\.git)?/?$")


class PublishError(RuntimeError):
    pass


def parse_remote(url: str) -> Dict[str, str]:
    text = str(url or "").strip()
    match = _URL_RE.match(text) or _SCP_RE.match(text)
    if not match:
        raise PublishError(f"unrecognised origin remote: {_redact(text)}")
    host, path = match.group("host").lower(), match.group("path").strip("/")
    kind = "github" if "github" in host else "gitlab" if "gitlab" in host else "unknown"
    return {"host": host, "path": path, "kind": kind}


def _redact(text: str) -> str:
    return re.sub(r"//[^@/]+@", "//***@", str(text or ""))


def _default_run(argv: list, cwd: str) -> Tuple[int, str, str]:
    proc = subprocess.run(argv, cwd=cwd or None, text=True, capture_output=True, timeout=300)
    return proc.returncode, proc.stdout, proc.stderr


def _default_post(url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    import httpx

    resp = httpx.post(url, headers=headers, json=body, timeout=30)
    if resp.status_code >= 400:
        raise PublishError(f"POST {url} failed: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()


def _pr_text(job: Job) -> Tuple[str, str]:
    jira = dict((job.metadata or {}).get("jira") or {})
    title = job.title[:200]
    lines = [job.description[:4000] if job.description else "", ""]
    if jira.get("url"):
        lines.append(f"Jira: {jira['url']}")
    if job.acceptance_criteria:
        lines += ["", "Acceptance criteria:", *[f"- {c}" for c in job.acceptance_criteria]]
    if job.validation_commands:
        lines += ["", "Validation:", *[f"- `{c}`" for c in job.validation_commands]]
    rounds = list((job.metadata or {}).get("review_rounds") or [])
    if rounds:
        lines += ["", f"Automated review: approved after {len(rounds)} round(s)."]
    lines += ["", f"Opened by MuCLI durable job `{job.id[:12]}` (draft)."]
    return title, "\n".join(lines).strip()


def open_draft_pr(job: Job, *, run: RunFn, post: PostFn, env: Dict[str, str], verified_head: str) -> Dict[str, Any]:
    repo = str((job.metadata or {}).get("repository_root") or job.repository or "")
    branch, base = str(job.branch or ""), str(job.base_branch or "main")
    if not repo or not branch:
        raise PublishError("job has no repository or review branch to publish")
    rc, out, err = run(["git", "remote", "get-url", "origin"], repo)
    if rc != 0:
        raise PublishError(f"no 'origin' remote: {err.strip()[:200]}")
    remote = parse_remote(out.strip())
    rc, _, err = run(["git", "push", "origin", f"{verified_head}:refs/heads/{branch}"], repo)
    if rc != 0:
        raise PublishError(f"git push failed: {_redact(err.strip())[:400]}")

    mode = str((job.execution or {}).get("publish_mode") or "auto").strip().lower()
    if mode == "auto":
        mode = "gh" if shutil.which("gh") and remote["kind"] == "github" else remote["kind"]
    title, body = _pr_text(job)
    pr: Dict[str, Any] = {"provider": mode, "draft": True, "branch": branch, "base": base}

    if mode == "gh":
        rc, out, err = run(["gh", "pr", "create", "--draft", "--base", base, "--head", branch,
                            "--title", title, "--body", body], repo)
        if rc != 0:
            raise PublishError(f"gh pr create failed: {err.strip()[:400]}")
        pr["url"] = out.strip().splitlines()[-1] if out.strip() else ""
    elif mode == "github":
        token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or ""
        if not token:
            raise PublishError("GITHUB_TOKEN (or GH_TOKEN) is not set")
        api = "https://api.github.com" if remote["host"] == "github.com" else f"https://{remote['host']}/api/v3"
        data = post(f"{api}/repos/{remote['path']}/pulls",
                    {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                    {"title": title, "body": body, "head": branch, "base": base, "draft": True})
        pr.update({"url": data.get("html_url", ""), "number": data.get("number")})
    elif mode == "gitlab":
        token = env.get("GITLAB_TOKEN") or ""
        if not token:
            raise PublishError("GITLAB_TOKEN is not set")
        data = post(f"https://{remote['host']}/api/v4/projects/{quote(remote['path'], safe='')}/merge_requests",
                    {"PRIVATE-TOKEN": token},
                    {"title": f"Draft: {title}", "description": body,
                     "source_branch": branch, "target_branch": base})
        pr.update({"url": data.get("web_url", ""), "number": data.get("iid")})
    else:
        raise PublishError(f"cannot open a PR for origin host {remote['host']!r} (publish_mode={mode})")
    return pr


def publish_job(service, job_id: str, *, run: Optional[RunFn] = None,
                post: Optional[PostFn] = None, env: Optional[Dict[str, str]] = None) -> int:
    from .stages import merge_metadata, publish_mode

    job = service.get(job_id)
    if job.status != JobStatus.PUBLISHING:
        return 0
    if publish_mode(job) == "none":
        service.transition(job_id, JobStatus.READY_FOR_REVIEW, reason="publishing disabled; skipped")
        return 0
    try:
        from .checks import push_check_reason
        from .verification import VerificationStore
        checked = VerificationStore(service.store).latest(job.id)
        check_reason = push_check_reason(service, job, run=checked)
        if check_reason:
            raise PublishError(check_reason)
        pr = open_draft_pr(job, run=run or _default_run, post=post or _default_post,
                           env=dict(os.environ if env is None else env), verified_head=checked.head_sha)
    except Exception as exc:  # noqa: BLE001 - any publish failure parks on a human
        service.require_human(job_id, AttentionReason.PUBLISH_FAILED,
                              f"Publishing failed: {_redact(str(exc))[:500]}",
                              payload={"stage": "publish"})
        return 27
    merge_metadata(service, job_id, pr=pr)
    service.store.append_event(job_id, "pr_opened", reason=pr.get("url", ""), payload=pr)
    service.transition(job_id, JobStatus.READY_FOR_REVIEW, reason="draft PR opened", payload=pr)
    return 0


def run_publish(job_id: str, worker_id: str, *, lease_ttl_seconds: int = 45) -> int:
    from .service import JobStateError, get_default_job_service
    from .verify_worker import _heartbeat

    service = get_default_job_service()
    stop, lost = threading.Event(), threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(service, job_id, worker_id, lease_ttl_seconds, stop, lost),
                            name=f"mucli-job-publish-heartbeat-{job_id[:8]}", daemon=True)
    try:
        job = service.get(job_id)
        if job.worker_id != worker_id or job.status != JobStatus.PUBLISHING:
            return 0
        beat.start()
        return publish_job(service, job_id)
    except JobStateError:
        return 0
    finally:
        stop.set()
        if beat.is_alive():
            beat.join(timeout=1.0)
        try:
            service.release(job_id, worker_id, reason="publish worker exited")
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="MuCLI durable job publish worker")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--lease-ttl", type=int, default=45)
    args = parser.parse_args()
    return run_publish(args.job_id, args.worker_id, lease_ttl_seconds=args.lease_ttl)


if __name__ == "__main__":
    raise SystemExit(main())
