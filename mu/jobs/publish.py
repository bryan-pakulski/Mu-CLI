"""Durable job v2 PUBLISHING stage: push the review branch, open a draft PR.

Runs after the reviewer approves a git-backed job. Provider is chosen from
``execution.publish_mode``:

* ``none``   - stage skipped (job goes straight to READY_FOR_REVIEW)
* ``auto``   - ``gh`` CLI if installed, else REST by ``origin`` host
* ``gh``     - GitHub CLI
* ``github`` - GitHub REST (``GITHUB_TOKEN`` / ``GH_TOKEN``)
* ``gitlab`` - GitLab REST merge request (``GITLAB_TOKEN``)
* ``bitbucket`` - Bitbucket Cloud REST pull request (``BITBUCKET_TOKEN``)
* ``gitea``  - Gitea/Forgejo REST pull request (``GITEA_TOKEN``)

The job's checkpoint commits are squashed onto the PR base into one commit
(job title, ticket key, ``Mucli-Job: <id>`` trailer; honours
``commit.gpgsign``). The push gate compares that commit's tree with the
verified tree, and the base..verified diff is secret-scanned before any push.
A re-push (after a re-prompt or a crash) updates the job's existing PR
(``metadata.pr`` or a host lookup by head branch) instead of opening another.

Settings: ``publish.push_remote`` / ``publish.pr_remote`` (default
``origin``), ``publish.provider_hosts`` (``host=github|gitlab|bitbucket|gitea``),
``publish.run_hooks`` (run the repo's ``commit-msg`` hook on the squashed
commit; default off).

PRs open as drafts unless the push dialog asked for ready-for-review
(``metadata.publish_override.draft = False``). Tokens come from the environment only and
are never written to the job record. Tokens come from the environment, else from the
publish token store (``mu.jobs.publish_tokens``: Settings → Push → Tokens,
``/job publish token``), which is host-side only and never reaches a job container.

Provider kind for the PR remote: ``publish.provider_hosts`` → host name
(github/gitlab/bitbucket/gitea/forgejo/codeberg) → ``publish.mode`` when it names a
provider → a ``https://<host>/api/v1/version`` / home-page probe that recognises
self-hosted Gitea/Forgejo, including sign-in-only instances (cached per host). Any failure parks the job on a human
with ``PUBLISH_FAILED``; the human READY_FOR_REVIEW gate is kept on success.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote

from .models import AttentionReason, Job, JobStatus
from .publish_tokens import TOKEN_VARS, token_source, with_tokens

RunFn = Callable[[list, str], Tuple[int, str, str]]
PostFn = Callable[[str, Dict[str, str], Dict[str, Any]], Dict[str, Any]]
GetFn = Callable[[str, Dict[str, str]], Any]

PROVIDER_KINDS = ("github", "gitlab", "bitbucket", "gitea")
_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

_SCP_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+?)(?:\.git)?/?$")
_URL_RE = re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+?)(?:\.git)?/?$")


class PublishError(RuntimeError):
    pass


def provider_hosts(value: Any = None) -> Dict[str, str]:
    """``publish.provider_hosts`` as ``{host: kind}`` (list of ``host=kind`` or a dict)."""
    if value is None:
        try:
            from .job_settings import rule_or
            value = rule_or("publish.provider_hosts", [])
        except Exception:  # noqa: BLE001
            value = []
    items = value.items() if isinstance(value, dict) else (
        str(entry).split("=", 1) for entry in (value or []) if "=" in str(entry))
    out: Dict[str, str] = {}
    for host, kind in items:
        host, kind = str(host).strip().lower(), str(kind).strip().lower()
        if host and kind in PROVIDER_KINDS:
            out[host] = kind
    return out


def _sniff_kind(host: str) -> str:
    if "github" in host:
        return "github"
    if "gitlab" in host:
        return "gitlab"
    if "bitbucket" in host:
        return "bitbucket"
    if "gitea" in host or "forgejo" in host or host == "codeberg.org":
        return "gitea"
    return "unknown"


def parse_remote(url: str, hosts: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    text = str(url or "").strip()
    match = _URL_RE.match(text) or _SCP_RE.match(text)
    if not match:
        raise PublishError(f"unrecognised origin remote: {_redact(text)}")
    host, path = match.group("host").lower(), match.group("path").strip("/")
    configured = provider_hosts() if hosts is None else hosts
    kind = configured.get(host) or _sniff_kind(host)
    return {"host": host, "path": path, "kind": kind}


_PROBE_CACHE: Dict[str, str] = {}


_GITEA_SIGNIN_MESSAGES = ("only signed in user is allowed to call apis", "token is required")


def _looks_like_gitea(status: int, data: Any, cookies: str, body: str = "") -> bool:
    if status == 200 and isinstance(data, dict) and str(data.get("version") or "").strip():
        return True
    message = str(data.get("message") or "").lower() if isinstance(data, dict) else ""
    if status in (401, 403) and any(m in message for m in _GITEA_SIGNIN_MESSAGES):
        return True  # REQUIRE_SIGNIN_VIEW instances refuse anonymous API calls with this message
    return "i_like_gitea" in cookies.lower() or ('content="gitea' in body.lower() or "forgejo" in body.lower()[:4000])


def probe_provider(host: str, *, timeout: float = 4.0) -> str:
    """Recognise a self-hosted Gitea/Forgejo without configuration.

    Tries ``https://<host>/api/v1/version`` (with ``GITEA_TOKEN`` when one is available, which
    sign-in-only instances need), accepting a version answer or Gitea's "only signed in user"
    refusal; then the home page's ``i_like_gitea`` session cookie. Returns ``"gitea"`` or
    ``"unknown"``; network errors count as unknown. Positive results are cached per host."""
    host = str(host or "").strip().lower()
    if not host:
        return "unknown"
    if host in _PROBE_CACHE:
        return _PROBE_CACHE[host]
    kind = "unknown"
    try:
        import httpx

        token = with_tokens().get("GITEA_TOKEN", "")
        headers = {"Accept": "application/json", **({"Authorization": f"token {token}"} if token else {})}
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(f"https://{host}/api/v1/version", headers=headers)
            try:
                data = resp.json()
            except ValueError:
                data = {}
            cookies = "; ".join(resp.headers.get_list("set-cookie"))
            if _looks_like_gitea(resp.status_code, data, cookies):
                kind = "gitea"
            else:
                home = client.get(f"https://{host}/")
                if _looks_like_gitea(home.status_code, {}, "; ".join(home.headers.get_list("set-cookie")),
                                     home.text[:20000]):
                    kind = "gitea"
    except Exception:  # noqa: BLE001 - offline / not Gitea: unknown
        kind = "unknown"
    if kind != "unknown":
        _PROBE_CACHE[host] = kind
    return kind


def resolve_kind(remote: Dict[str, str], *, probe: bool = True) -> str:
    """Provider kind for a parsed remote (order in the module docstring)."""
    kind = str(remote.get("kind") or "unknown")
    if kind in PROVIDER_KINDS:
        return kind
    try:
        from .job_settings import rule_or
        configured = str(rule_or("publish.mode", "") or "").strip().lower()
    except Exception:  # noqa: BLE001
        configured = ""
    if configured in PROVIDER_KINDS:
        return configured
    return probe_provider(remote.get("host", "")) if probe else "unknown"


def _unknown_host_message(host: str, mode: str) -> str:
    return (f"couldn't tell which kind of git host {host!r} is (publish mode {mode}): its name doesn't say, and "
            f"https://{host} doesn't look like Gitea/Forgejo. Add `{host}=gitea` (or gitlab, "
            "github, bitbucket) to Settings → Push → PR provider hosts, or choose the publish mode there.")


def _publish_settings() -> Dict[str, Any]:
    try:
        from .job_settings import rule_or
        return {
            "push_remote": str(rule_or("publish.push_remote", "origin") or "origin").strip() or "origin",
            "pr_remote": str(rule_or("publish.pr_remote", "origin") or "origin").strip() or "origin",
            "run_hooks": bool(rule_or("publish.run_hooks", False)),
        }
    except Exception:  # noqa: BLE001
        return {"push_remote": "origin", "pr_remote": "origin", "run_hooks": False}


def _redact(text: str) -> str:
    return re.sub(r"//[^@/]+@", "//***@", str(text or ""))


def _default_run(argv: list, cwd: str) -> Tuple[int, str, str]:
    from .job_settings import rule_or
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    timeout = int(rule_or("publish.timeout_s", 3600))
    if argv[:2] == ["git", "fetch"]:
        timeout = min(timeout, 30)  # best effort: an offline host must not stall publishing
    try:
        proc = subprocess.run(argv, cwd=cwd or None, text=True, capture_output=True, timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(map(str, argv[:2]))} timed out after {timeout}s"
    return proc.returncode, proc.stdout, proc.stderr


def _default_get(url: str, headers: Dict[str, str]) -> Any:
    import httpx

    resp = httpx.get(url, headers=headers, timeout=30)
    if resp.status_code >= 400:
        raise PublishError(f"GET {url} failed: HTTP {resp.status_code}")
    return resp.json()


def _local_git(repo: str, *args: str, env: Optional[Dict[str, str]] = None, input: Optional[str] = None,
               check: bool = True) -> str:
    """Local (no network) git in the canonical repository; hooks disabled."""
    from .repository import git_timeout_seconds
    command = ["git", "-c", "core.hooksPath=/dev/null", "-C", repo, *args]
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=git_timeout_seconds(),
                              env=env, input=input, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PublishError(f"git {args[0] if args else ''} timed out (jobs.git_timeout_seconds)") from exc
    except OSError as exc:
        raise PublishError(f"could not run git: {exc}") from exc
    if check and proc.returncode != 0:
        raise PublishError(f"git {' '.join(args[:2])} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _default_post(url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    import httpx

    from .job_settings import rule_or
    resp = httpx.post(url, headers=headers, json=body, timeout=int(rule_or("publish.timeout_s", 3600)))
    if resp.status_code >= 400:
        raise PublishError(f"POST {url} failed: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()


def _pr_text(job: Job) -> Tuple[str, str]:
    from mu.integrations.jira import strip_untrusted_frame, ticket_ref

    ticket = ticket_ref(job.metadata or {})
    title = job.title[:200]
    description = strip_untrusted_frame(job.description or "")
    lines = [description[:4000], ""]
    if ticket.get("url"):
        label = "Jira" if ticket.get("tracker", "jira") == "jira" else "Issue"
        lines.append(f"{label}: {ticket['url']}")
    if job.acceptance_criteria:
        lines += ["", "Acceptance criteria:", *[f"- {c}" for c in job.acceptance_criteria]]
    if job.validation_commands:
        lines += ["", "Validation:", *[f"- `{c}`" for c in job.validation_commands]]
    from .core.artifacts import artifacts_for
    report = artifacts_for(job).read_json("review.json")
    if report:
        lines += ["", f"Review report: {report.get('verdict', 'unknown')} · readiness {report.get('merge_readiness', 0)}/100."]
    lines += ["", f"Opened by MuCLI durable job `{job.id[:12]}` (draft)."]
    return title, "\n".join(lines).strip()


def _publish_text(job: Job, *, unverified: str = "") -> Tuple[str, str, bool]:
    """PR title/body/draft: push-dialog overrides over ``_pr_text`` defaults. ``unverified``
    (:func:`unverified_note`) heads the body, even an edited one."""
    title, body = _pr_text(job)
    override = (job.metadata or {}).get("publish_override")
    draft = True
    if isinstance(override, dict):
        if str(override.get("title") or "").strip():
            title = str(override["title"]).strip()[:200]
        if str(override.get("body") or "").strip():
            body = str(override["body"]).strip()[:20000]
        draft = override.get("draft") is not False
        if not draft and not str(override.get("body") or "").strip():
            body = body.replace(f"`{job.id[:12]}` (draft).", f"`{job.id[:12]}`.")
    if unverified and unverified not in body:
        body = f"{unverified}\n\n{body}"
    return title, body, draft


def _resolve_mode(job: Job, remote_kind: str) -> str:
    mode = str((job.execution or {}).get("publish_mode") or "auto").strip().lower()
    if mode == "auto":
        mode = "gh" if shutil.which("gh") and remote_kind == "github" else remote_kind
    return mode


def _strip_remote(ref: str, remote: str) -> str:
    value = str(ref or "").strip()
    for prefix in ("refs/heads/", f"refs/remotes/{remote}/", f"{remote}/", "origin/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


def resolve_pr_base(repo: str, base_branch: str, *, remote: str = "origin", default: str = "") -> str:
    """PR target branch: the upstream of ``base_branch`` (``origin/`` stripped),
    else the same branch when the remote has it, else the remote's default
    branch, else ``default``/the bare name. Local-only (no network)."""
    name = _strip_remote(base_branch, remote)
    if name.lower() in {"", "auto", "head"}:
        name = ""
    if repo and name:
        upstream = _local_git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{name}@{{upstream}}",
                              check=False)
        if upstream and "/" in upstream:
            return upstream.split("/", 1)[1]
        if _local_git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{name}", check=False):
            return name
    if repo:
        head = _local_git(repo, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD", check=False)
        if head and "/" in head:
            return head.split("/", 1)[1]
    return name or _strip_remote(default, remote) or "main"


def _ticket_key(job: Job) -> str:
    meta = dict(job.metadata or {})
    for key in ("ticket", "jira"):
        block = meta.get(key)
        if isinstance(block, dict) and str(block.get("key") or "").strip():
            return str(block["key"]).strip()[:64]
    return ""


def squash_message(job: Job) -> str:
    """Subject = ticket key + job title; body = description summary; trailer ``Mucli-Job``."""
    title = " ".join(str(job.title or "MuCLI job").split())[:200]
    key = _ticket_key(job)
    subject = f"{key}: {title}" if key and key.lower() not in title.lower() else title
    from mu.integrations.jira import strip_untrusted_frame

    body = strip_untrusted_frame(str(job.description or "")).strip()
    if len(body) > 2000:
        body = body[:2000].rstrip() + "\n[...]"
    parts = [subject]
    if body:
        parts.append(body)
    parts.append(f"Mucli-Job: {job.id}")
    return "\n\n".join(parts) + "\n"


def _run_commit_msg_hook(repo: str, message: str) -> str:
    """Run the repository's ``commit-msg`` hook on the squashed message (opt-in)."""
    from .repository import git_timeout_seconds
    try:  # no core.hooksPath override here: resolve the repository's real hook location
        proc = subprocess.run(["git", "-C", repo, "rev-parse", "--git-path", "hooks/commit-msg"], text=True,
                              capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return message
    hook = proc.stdout.strip() if proc.returncode == 0 else ""
    if not hook:
        return message
    hook = hook if os.path.isabs(hook) else os.path.join(repo, hook)
    if not (os.path.isfile(hook) and os.access(hook, os.X_OK)):
        return message
    with tempfile.NamedTemporaryFile("w+", suffix="-COMMIT_EDITMSG", delete=False, encoding="utf-8") as handle:
        handle.write(message)
        path = handle.name
    try:
        proc = subprocess.run([hook, path], cwd=repo, text=True, capture_output=True,
                              timeout=git_timeout_seconds(), check=False)
        if proc.returncode != 0:
            raise PublishError(f"commit-msg hook rejected the squashed commit: {(proc.stderr or proc.stdout).strip()[:300]}")
        with open(path, encoding="utf-8") as handle:
            return handle.read() or message
    except subprocess.TimeoutExpired as exc:
        raise PublishError("commit-msg hook timed out") from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def squash_for_publish(job: Job, repo: str, verified_head: str, base_ref: str, *, fallback_base: str = "",
                       run_hooks: bool = False) -> Dict[str, str]:
    """One commit with the verified tree on top of the PR base.

    Returns ``{"sha", "base", "tree"}``. ``base`` is the merge-base of the
    verified head and the current base tip (so a host merge of the base is
    not re-attributed to the PR). Nothing to squash -> the verified commit
    itself. Raises unless the result's tree equals the verified tree."""
    from .worktree import git_identity_env

    tree = _local_git(repo, "rev-parse", "--verify", f"{verified_head}^{{tree}}")
    base = ""
    if base_ref and _local_git(repo, "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}", check=False):
        base = _local_git(repo, "merge-base", base_ref, verified_head, check=False)
    if not base and fallback_base:
        base = _local_git(repo, "merge-base", fallback_base, verified_head, check=False)
    if not base:
        raise PublishError("cannot determine the base commit to squash the job onto")
    count = int(_local_git(repo, "rev-list", "--count", f"{base}..{verified_head}") or 0)
    if count == 0:
        sha = _local_git(repo, "rev-parse", "--verify", f"{verified_head}^{{commit}}")
    else:
        message = squash_message(job)
        if run_hooks:
            message = _run_commit_msg_hook(repo, message)
        signed = _local_git(repo, "config", "--bool", "commit.gpgsign", check=False) == "true"
        # Deterministic squash: author/committer dates come from the verified
        # commit, so a retry after a crash between push and the pushed_sha
        # metadata write recomputes the SAME sha (unsigned, hook-free case).
        env = git_identity_env(repo)
        stamp = _local_git(repo, "show", "-s", "--format=%ct %cz", f"{verified_head}^{{commit}}", check=False)
        if stamp:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{stamp}"
        sha = _local_git(repo, "commit-tree", tree, "-p", base, "-S" if signed else "--no-gpg-sign", "-F", "-",
                         env=env, input=message)
    # Push gate after squash: the pushed commit must carry exactly the verified tree.
    if _local_git(repo, "rev-parse", "--verify", f"{sha}^{{tree}}") != tree:
        raise PublishError("squashed commit tree differs from the verified tree; refusing to push")
    return {"sha": sha, "base": base, "tree": tree, "squashed": "true" if count else "false"}


def scan_publish_diff(repo: str, base: str, head: str, *, literals=()) -> None:
    """Refuse to push a diff that adds something that looks like a secret (or one of the
    credential ``literals`` the host injected into the job's stages). ``--text`` so binary
    files and ``-diff`` attributed paths are scanned too; no external diff or textconv."""
    from mu.security.diff_scan import scan_patch, summarize
    from .repository import git_timeout_seconds
    try:  # bytes: a --text diff of a binary file is not valid UTF-8
        proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", repo, "diff", "--no-color", "--text",
                               "--no-ext-diff", "--no-textconv", base, head, "--"],
                              capture_output=True, timeout=git_timeout_seconds(), check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublishError(f"could not diff the publish range: {exc}") from exc
    if proc.returncode != 0:
        raise PublishError(f"git diff failed: {proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    patch = proc.stdout.decode("utf-8", errors="replace")
    findings = scan_patch(patch, literals=literals, allow_fixtures=True)
    if findings:
        raise PublishError(f"refusing to push: {summarize(findings)}")


def _api_base(kind: str, host: str) -> str:
    if kind == "github":
        return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    if kind == "gitlab":
        return f"https://{host}/api/v4"
    if kind == "bitbucket":
        return "https://api.bitbucket.org/2.0" if host == "bitbucket.org" else f"https://{host}/api/2.0"
    return f"https://{host}/api/v1"


def _auth(mode: str, env: Dict[str, str]) -> Optional[Dict[str, str]]:
    if mode == "github":
        token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or ""
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"} if token else None
    if mode == "gitlab":
        token = env.get("GITLAB_TOKEN") or ""
        return {"PRIVATE-TOKEN": token} if token else None
    if mode == "bitbucket":
        token = env.get("BITBUCKET_TOKEN") or ""
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"} if token else None
    if mode == "gitea":
        token = env.get("GITEA_TOKEN") or ""
        return {"Authorization": f"token {token}", "Accept": "application/json"} if token else None
    return None


_TOKEN_NAMES = {"github": "GITHUB_TOKEN (or GH_TOKEN)", "gitlab": "GITLAB_TOKEN",
                "bitbucket": "BITBUCKET_TOKEN", "gitea": "GITEA_TOKEN"}


def find_open_pr(mode: str, remote: Dict[str, str], branch: str, head_ref: str, *, repo: str, run: RunFn,
                 get: Optional[GetFn], env: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """An open PR/MR whose head is the job branch, or ``None`` (best effort)."""
    try:
        if mode == "gh":
            rc, out, _ = run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url,number",
                              "--limit", "1"], repo)
            rows = json.loads(out) if rc == 0 and out.strip().startswith("[") else []
            return {"url": rows[0].get("url", ""), "number": rows[0].get("number")} if rows else None
        headers = _auth(mode, env)
        if get is None or headers is None:
            return None
        api, path = _api_base(mode, remote["host"]), remote["path"]
        if mode == "github":
            owner_head = head_ref if ":" in head_ref else f"{path.split('/', 1)[0]}:{branch}"
            rows = get(f"{api}/repos/{path}/pulls?state=open&head={quote(owner_head, safe=':')}", headers)
            return {"url": rows[0].get("html_url", ""), "number": rows[0].get("number")} if rows else None
        if mode == "gitlab":
            rows = get(f"{api}/projects/{quote(path, safe='')}/merge_requests?state=opened"
                       f"&source_branch={quote(branch, safe='')}", headers)
            return {"url": rows[0].get("web_url", ""), "number": rows[0].get("iid")} if rows else None
        if mode == "bitbucket":
            query = quote(f'source.branch.name="{branch}" AND state="OPEN"', safe="")
            rows = (get(f"{api}/repositories/{path}/pullrequests?q={query}", headers) or {}).get("values") or []
            return ({"url": ((rows[0].get("links") or {}).get("html") or {}).get("href", ""), "number": rows[0].get("id")}
                    if rows else None)
        if mode == "gitea":
            rows = get(f"{api}/repos/{path}/pulls?state=open&limit=50", headers) or []
            for row in rows:
                if ((row.get("head") or {}).get("ref") or "") == branch:
                    return {"url": row.get("html_url", ""), "number": row.get("number")}
    except Exception:  # noqa: BLE001 - lookup is best effort; creating may still fail loudly
        return None
    return None


def _create_pr(mode: str, remote: Dict[str, str], *, branch: str, head_ref: str, base: str, title: str, body: str,
               draft: bool, repo: str, run: RunFn, post: PostFn, env: Dict[str, str]) -> Dict[str, Any]:
    if mode == "gh":
        rc, out, err = run(["gh", "pr", "create", *(["--draft"] if draft else []), "--base", base, "--head", head_ref,
                            "--title", title, "--body", body], repo)
        if rc != 0:
            raise PublishError(f"gh pr create failed: {err.strip()[:400]}")
        return {"url": out.strip().splitlines()[-1] if out.strip() else ""}
    if mode not in PROVIDER_KINDS:
        raise PublishError(_unknown_host_message(remote["host"], mode))
    headers = _auth(mode, env)
    if headers is None:
        raise PublishError(f"{_TOKEN_NAMES[mode]} is not set: add a {mode} token in Settings → Push → Tokens "
                           f"(or `/job publish token {mode} <token>`, or export it before starting mucli). "
                           "The branch is pushed; push again to open the "
                           f"{'merge request' if mode == 'gitlab' else 'pull request'}.")
    api, path = _api_base(mode, remote["host"]), remote["path"]
    if mode == "github":
        data = post(f"{api}/repos/{path}/pulls", headers,
                    {"title": title, "body": body, "head": head_ref, "base": base, "draft": draft})
        return {"url": data.get("html_url", ""), "number": data.get("number")}
    if mode == "gitlab":
        data = post(f"{api}/projects/{quote(path, safe='')}/merge_requests", headers,
                    {"title": f"Draft: {title}" if draft else title, "description": body,
                     "source_branch": branch, "target_branch": base})
        return {"url": data.get("web_url", ""), "number": data.get("iid")}
    if mode == "bitbucket":
        data = post(f"{api}/repositories/{path}/pullrequests", headers,
                    {"title": title, "description": body, "draft": draft, "close_source_branch": False,
                     "source": {"branch": {"name": branch}}, "destination": {"branch": {"name": base}}})
        return {"url": ((data.get("links") or {}).get("html") or {}).get("href", ""), "number": data.get("id")}
    data = post(f"{api}/repos/{path}/pulls", headers,
                {"title": f"WIP: {title}" if draft else title, "body": body, "head": head_ref, "base": base})
    return {"url": data.get("html_url", ""), "number": data.get("number")}


def publish_preview(service, job: Job) -> Dict[str, Any]:
    """What a push would open, for the push dialog (contract §2). Read-only (no fetch)."""
    from .checks import push_check_reason
    from .core.artifacts import artifacts_for
    from .stages import publish_mode
    from .verification import VerificationStore

    title, body = _pr_text(job)
    meta = dict(job.metadata or {})
    repo = str(meta.get("repository_root") or job.repository or "")
    settings = _publish_settings()
    remote_url, kind = "", "unknown"
    base = str(job.base_branch or "main")
    if repo:
        try:
            proc = subprocess.run(["git", "-C", repo, "remote", "get-url", settings["pr_remote"]], text=True,
                                  capture_output=True, timeout=5)
            if proc.returncode == 0:
                remote_url = proc.stdout.strip()
                kind = resolve_kind(parse_remote(remote_url))
        except (OSError, subprocess.SubprocessError, PublishError):
            pass
        try:
            base = resolve_pr_base(repo, base, remote=settings["pr_remote"],
                                   default=str(meta.get("repository_default_branch") or ""))
        except PublishError:
            pass
    mode = "none" if publish_mode(job) == "none" else _resolve_mode(job, kind)
    report = artifacts_for(job).read_json("review.json") or {}
    verdict = str(report.get("verdict") or "")
    run = VerificationStore(service.store).latest(job.id)
    unverified = run is not None and not run.verified
    try:
        reason = push_check_reason(service, job, run=run, allow_unverified=True)
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)[:300] or "cannot verify checks"
    note = unverified_note(run)
    return {
        "title": title, "body": f"{note}\n\n{body}" if note else body,
        "branch": str(job.branch or meta.get("review_branch") or ""),
        "base": base,
        "mode": mode, "remote": _redact(remote_url), "draft": True,
        # Unverified checks (job_templates.md §5) need no override; the PR body warns instead.
        "requires_override": verdict == "do_not_merge", "verdict": verdict,
        "head_sha": str(run.head_sha or "") if run is not None and (run.passed or unverified) else "",
        "reason": reason,
        # The token the PR/MR step will use (never the value), so the dialog can warn before pushing.
        "token": ({"provider": mode, "variable": TOKEN_VARS[mode][0], "source": token_source(mode)}
                  if mode in TOKEN_VARS else None),
        "provider": kind,
    }


def _is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    try:
        return subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", ancestor, descendant],
                              capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _remote_branch_sha(run: RunFn, repo: str, remote: str, branch: str) -> str:
    rc, out, _ = run(["git", "ls-remote", remote, f"refs/heads/{branch}"], repo)
    first = (out or "").split()[0] if rc == 0 and (out or "").split() else ""
    return first if _SHA_RE.match(first) else ""


def _is_equivalent_squash(run: RunFn, repo: str, remote: str, branch: str, remote_sha: str,
                          squash: Dict[str, str]) -> bool:
    """True when ``remote_sha`` carries exactly the verified tree with the expected base as its only parent."""
    if not _local_git(repo, "rev-parse", "--verify", "--quiet", f"{remote_sha}^{{commit}}", check=False):
        run(["git", "fetch", "--quiet", "--no-tags", remote, f"refs/heads/{branch}"], repo)
    tree = _local_git(repo, "rev-parse", "--verify", "--quiet", f"{remote_sha}^{{tree}}", check=False)
    parents = _local_git(repo, "rev-list", "--parents", "-n", "1", remote_sha, check=False).split()[1:]
    return bool(tree) and tree == squash.get("tree") and parents == [squash.get("base")]


UNVERIFIED_PREFIX = "⚠ Checks could not run (unverified)"


def unverified_note(run: Any) -> str:
    """``⚠ Checks could not run (unverified): <tools/causes>`` for an unverified check run, else ''."""
    if run is None or getattr(run, "verified", True):
        return ""
    from .checks import unverified_causes
    causes = unverified_causes(dict(getattr(run, "summary", None) or {}))
    return f"{UNVERIFIED_PREFIX}: {causes or 'some checks could not run'}"


def open_draft_pr(job: Job, *, run: RunFn, post: PostFn, env: Dict[str, str], verified_head: str,
                  get: Optional[GetFn] = None, base_sha: str = "",
                  on_pushed: Optional[Callable[[Dict[str, Any]], None]] = None,
                  unverified: str = "") -> Dict[str, Any]:
    meta = dict(job.metadata or {})
    repo = str(meta.get("repository_root") or job.repository or "")
    branch = str(job.branch or "")
    if not repo or not branch:
        raise PublishError("job has no repository or review branch to publish")
    settings = _publish_settings()
    push_remote, pr_remote = settings["push_remote"], settings["pr_remote"]
    rc, out, err = run(["git", "remote", "get-url", pr_remote], repo)
    if rc != 0:
        raise PublishError(f"no {pr_remote!r} remote: {err.strip()[:200]}")
    remote = parse_remote(out.strip())
    remote["kind"] = resolve_kind(remote)
    head_ref = branch
    if push_remote != pr_remote:
        rc, push_out, err = run(["git", "remote", "get-url", push_remote], repo)
        if rc != 0:
            raise PublishError(f"no {push_remote!r} remote: {err.strip()[:200]}")
        try:
            push_owner = parse_remote(push_out.strip())["path"].split("/", 1)[0]
        except PublishError:  # a local/mirror push remote: same-repo head
            push_owner = remote["path"].split("/", 1)[0]
        if push_owner != remote["path"].split("/", 1)[0]:
            head_ref = f"{push_owner}:{branch}"  # fork: GitHub/Gitea head is owner:branch

    # Base: best-effort refresh (offline tolerated), then the upstream of the job's base.
    base_name = resolve_pr_base(repo, str(job.base_branch or ""), remote=pr_remote,
                                default=str(meta.get("repository_default_branch") or ""))
    run(["git", "fetch", "--quiet", "--no-tags", pr_remote,
         f"+refs/heads/{base_name}:refs/remotes/{pr_remote}/{base_name}"], repo)
    base = resolve_pr_base(repo, str(job.base_branch or ""), remote=pr_remote,
                           default=str(meta.get("repository_default_branch") or ""))
    tracking = f"refs/remotes/{pr_remote}/{base}"
    base_ref = tracking if _local_git(repo, "rev-parse", "--verify", "--quiet", tracking, check=False) else base
    squash = squash_for_publish(job, repo, verified_head, base_ref, fallback_base=base_sha or str(job.base_sha or ""),
                                run_hooks=settings["run_hooks"])
    from .sandbox.runner import job_injected_literals
    scan_publish_diff(repo, squash["base"], verified_head, literals=job_injected_literals(job))
    sha = squash["sha"]

    # Re-push: only ever replace what this job pushed itself.
    remote_sha = _remote_branch_sha(run, repo, push_remote, branch)
    previous = {str(meta.get("pushed_sha") or ""), str((meta.get("pr") or {}).get("head_sha") or "")} - {""}
    if remote_sha and remote_sha != sha and remote_sha not in previous \
            and _is_equivalent_squash(run, repo, push_remote, branch, remote_sha, squash):
        # A crash between the push and the pushed_sha write left our own
        # squash (same verified tree on the same base, but a different sha —
        # e.g. signed or hook-stamped) on the remote: adopt it as pushed.
        sha = remote_sha
    push = ["git", "push", push_remote, f"{sha}:refs/heads/{branch}"]
    if remote_sha and remote_sha != sha:
        if not _is_ancestor(repo, remote_sha, sha):
            if remote_sha not in previous:
                raise PublishError(f"remote branch {branch!r} has commits this job did not push "
                                   f"({remote_sha[:12]}); refusing to overwrite it")
            push = ["git", "push", f"--force-with-lease=refs/heads/{branch}:{remote_sha}", push_remote,
                    f"{sha}:refs/heads/{branch}"]
    rc, _, err = run(push, repo)
    if rc != 0:
        raise PublishError(f"git push failed: {_redact(err.strip())[:400]}")
    if on_pushed is not None:
        on_pushed({"pushed_sha": sha, "verified_sha": verified_head})

    mode = _resolve_mode(job, remote["kind"])
    title, body, draft = _publish_text(job, unverified=unverified)
    pr: Dict[str, Any] = {"provider": mode, "draft": draft, "branch": branch, "base": base,
                          "head_sha": sha, "verified_sha": verified_head, "tree": squash["tree"],
                          "squashed": squash["squashed"] == "true"}
    existing = meta.get("pr") if isinstance(meta.get("pr"), dict) else None
    if existing and existing.get("state") == "closed":
        # A PR closed without merging is never reused: re-push opens a new one.
        pr["replaces"] = str(existing.get("url") or existing.get("number") or "")
        existing = None
    if existing and (existing.get("url") or existing.get("number")):
        found: Optional[Dict[str, Any]] = {"url": existing.get("url", ""), "number": existing.get("number")}
    else:
        found = find_open_pr(mode, remote, branch, head_ref, repo=repo, run=run, get=get, env=env)
    if found:
        pr.update({k: v for k, v in found.items() if v not in (None, "")}, updated=True)
        if existing:
            pr["draft"] = existing.get("draft", draft)
        return pr
    pr.update(_create_pr(mode, remote, branch=branch, head_ref=head_ref, base=base, title=title, body=body,
                         draft=draft, repo=repo, run=run, post=post, env=env))
    return pr


def publish_job(service, job_id: str, *, run: Optional[RunFn] = None,
                post: Optional[PostFn] = None, env: Optional[Dict[str, str]] = None,
                get: Optional[GetFn] = None) -> int:
    from .stages import merge_metadata, publish_mode

    job = service.get(job_id)
    if job.status != JobStatus.PUBLISHING:
        return 0
    if publish_mode(job) == "none":
        service.transition(job_id, JobStatus.READY_FOR_REVIEW, reason="publishing disabled; skipped")
        _release_environments(service, job_id)
        return 0
    # Lookups of an existing PR use real HTTP only with the real transport; a
    # caller injecting a fake ``post`` must inject ``get`` too.
    get = get if get is not None else (_default_get if post is None else None)
    try:
        from .checks import push_check_reason
        from .verification import VerificationStore
        checked = VerificationStore(service.store).latest(job.id)
        # An unverified run (unrunnable checks) publishes without an override (jobs run
        # unattended); the PR body states that the checks could not run (job_templates.md §5).
        check_reason = push_check_reason(service, job, run=checked, allow_unverified=True)
        if check_reason:
            raise PublishError(check_reason)
        pr = open_draft_pr(job, run=run or _default_run, post=post or _default_post,
                           env=with_tokens(env), verified_head=checked.head_sha,
                           unverified=unverified_note(checked),
                           get=get, base_sha=str(checked.base_sha or ""),
                           on_pushed=lambda info: merge_metadata(service, job_id, **info))
    except Exception as exc:  # noqa: BLE001 - any publish failure parks on a human
        service.require_human(job_id, AttentionReason.PUBLISH_FAILED,
                              f"Publishing failed: {_redact(str(exc))[:500]}",
                              payload={"stage": "publish"})
        return 27
    updated = bool(pr.pop("updated", False))
    merge_metadata(service, job_id, pr=pr)
    event = "pr_updated" if updated else "pr_opened"
    service.store.append_event(job_id, event, reason=pr.get("url", ""), payload=pr)
    service.transition(job_id, JobStatus.READY_FOR_REVIEW,
                       reason="existing PR updated" if updated else "draft PR opened", payload=pr)
    _release_environments(service, job_id)
    return 0


def _get_quick(url: str, headers: Dict[str, str]) -> Any:
    """Bounded GET for the merge poller (short timeout; never blocks the controller long)."""
    import httpx

    resp = httpx.get(url, headers=headers, timeout=15)
    if resp.status_code >= 400:
        raise PublishError(f"GET {url} failed: HTTP {resp.status_code}")
    return resp.json()


def _run_quick(argv: list, cwd: str) -> Tuple[int, str, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1")
    try:
        proc = subprocess.run(argv, cwd=cwd or None, text=True, capture_output=True, timeout=30, env=env,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(map(str, argv[:3]))} timed out"
    except OSError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _number_from_url(url: str) -> str:
    match = re.search(r"/(?:pull|pulls|merge_requests|pull-requests|pullrequests)/(\d+)", str(url or ""))
    return match.group(1) if match else ""


def check_pr_state(job: Job, *, run: Optional[RunFn] = None, get: Optional[GetFn] = None,
                   env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Host state of the job's PR: ``{"state": open|merged|closed|unknown, "merged_at", "detail"}``.

    Uses ``gh pr view`` for gh-published PRs, else the provider REST API with the
    same token environment variables as publishing. Read-only; raises nothing."""
    run = run or _run_quick
    get = get or _get_quick
    env = with_tokens(env)
    meta = dict(job.metadata or {})
    pr = meta.get("pr") if isinstance(meta.get("pr"), dict) else {}
    url = str(pr.get("url") or "")
    number = str(pr.get("number") or "") or _number_from_url(url)
    repo = str(meta.get("repository_root") or job.repository or "")
    mode = str(pr.get("provider") or "").strip().lower()
    unknown = {"state": "unknown", "merged_at": "", "detail": ""}
    if not (url or number):
        return {**unknown, "detail": "job has no PR"}
    try:
        if mode == "gh" or (not mode and shutil.which("gh") and "github" in url):
            rc, out, err = run(["gh", "pr", "view", url or number, "--json", "state,mergedAt"], repo)
            if rc != 0:
                return {**unknown, "detail": f"gh pr view failed: {_redact(err.strip())[:200]}"}
            data = json.loads(out or "{}")
            state = str(data.get("state") or "").upper()
            merged_at = str(data.get("mergedAt") or "")
            if state == "MERGED" or merged_at:
                return {"state": "merged", "merged_at": merged_at, "detail": ""}
            return {"state": "closed" if state == "CLOSED" else "open" if state == "OPEN" else "unknown",
                    "merged_at": "", "detail": ""}
        rc, out, _ = run(["git", "remote", "get-url", _publish_settings()["pr_remote"]], repo)
        if rc != 0:
            return {**unknown, "detail": "no PR remote"}
        remote = parse_remote(out.strip())
        kind = mode if mode in PROVIDER_KINDS else resolve_kind(remote)
        headers = _auth(kind, env)
        if headers is None or not number:
            return {**unknown, "detail": f"{_TOKEN_NAMES.get(kind, 'a token')} is not set" if headers is None
                    else "PR number unknown"}
        api, path = _api_base(kind, remote["host"]), remote["path"]
        if kind == "github" or kind == "gitea":
            data = get(f"{api}/repos/{path}/pulls/{number}", headers) or {}
            if data.get("merged") or data.get("merged_at"):
                return {"state": "merged", "merged_at": str(data.get("merged_at") or ""), "detail": ""}
            return {"state": "closed" if data.get("state") == "closed" else "open", "merged_at": "", "detail": ""}
        if kind == "gitlab":
            data = get(f"{api}/projects/{quote(path, safe='')}/merge_requests/{number}", headers) or {}
            state = str(data.get("state") or "")
            if state == "merged":
                return {"state": "merged", "merged_at": str(data.get("merged_at") or ""), "detail": ""}
            return {"state": "closed" if state in ("closed", "locked") else "open", "merged_at": "", "detail": ""}
        if kind == "bitbucket":
            data = get(f"{api}/repositories/{path}/pullrequests/{number}", headers) or {}
            state = str(data.get("state") or "").upper()
            if state == "MERGED":
                return {"state": "merged", "merged_at": str(data.get("updated_on") or ""), "detail": ""}
            return {"state": "closed" if state in ("DECLINED", "SUPERSEDED") else "open", "merged_at": "", "detail": ""}
    except Exception as exc:  # noqa: BLE001 - polling is best effort
        return {**unknown, "detail": _redact(str(exc))[:200]}
    return {**unknown, "detail": f"unsupported PR provider {mode or 'unknown'}"}


def merge_poll_seconds() -> float:
    """``publish.merge_poll_seconds`` (default 300; 0 = off)."""
    try:
        from .job_settings import rule_or
        return max(0.0, float(rule_or("publish.merge_poll_seconds", 300) or 0))
    except Exception:  # noqa: BLE001
        return 300.0


def poll_merged_prs(service, *, limit: int = 10, run: Optional[RunFn] = None, get: Optional[GetFn] = None,
                    env: Optional[Dict[str, str]] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """One bounded pass over pushed jobs with a PR (READY_FOR_REVIEW, and
    CONFLICTED: local drift checks can flag a branch the host already merged).

    Merged → DONE through the normal transition path (tracker writeback fires),
    whatever the local drift said; closed without merging → the PR is flagged
    ``state=closed`` with a ``pr_closed`` event and the job parks on a human
    (``needs_human`` / ``pr_closed``) with Re-push and Mark done actions.
    Jobs are polled least-recently-checked first; failures are logged, never raised."""
    import logging
    import time as _time

    log = logging.getLogger(__name__)
    now = _time.time() if now is None else float(now)
    out: Dict[str, Any] = {"checked": [], "merged": [], "closed": [], "errors": []}
    polled = (JobStatus.READY_FOR_REVIEW, JobStatus.CONFLICTED)
    try:
        pushed = service.list(statuses=list(polled), limit=500)
    except Exception as exc:  # noqa: BLE001
        log.debug("merge poll: list failed: %s", exc)
        return out
    candidates = []
    for job in pushed:
        pr = (job.metadata or {}).get("pr")
        if not isinstance(pr, dict) or not (pr.get("url") or pr.get("number")):
            continue
        if pr.get("state") == "closed":
            continue  # already flagged; a re-push clears the flag (new pr metadata)
        candidates.append((float(pr.get("checked_at") or 0.0), job))
    candidates.sort(key=lambda item: item[0])
    for _, job in candidates[:max(1, int(limit))]:
        try:
            polled_pr = dict((job.metadata or {}).get("pr") or {})
            result = check_pr_state(job, run=run, get=get, env=env)
            out["checked"].append(job.id)
            current = service.get(job.id)
            if current.status not in polled:
                continue
            ident = (polled_pr.get("url"), polled_pr.get("number"))

            def stamp(meta: Dict[str, Any]) -> None:
                pr_now = meta.get("pr") if isinstance(meta.get("pr"), dict) else None
                if not pr_now or (pr_now.get("url"), pr_now.get("number")) != ident:
                    return  # a re-push replaced the PR record meanwhile
                pr_now = dict(pr_now, checked_at=now)
                if result["state"] in ("merged", "closed", "open"):
                    pr_now["state"] = result["state"]
                if result.get("merged_at"):
                    pr_now["merged_at"] = result["merged_at"]
                meta["pr"] = pr_now

            # Atomic nested update: never loses concurrent metadata writes.
            fresh = service.store.patch_metadata(job.id, mutate=stamp)
            pr = dict((fresh.metadata or {}).get("pr") or {})
            if (pr.get("url"), pr.get("number")) != ident:
                continue
            if result["state"] == "merged":
                service.store.append_event(job.id, "pr_merged", reason=str(pr.get("url") or ""),
                                           payload={"url": pr.get("url", ""), "merged_at": result.get("merged_at", "")})
                service.transition(job.id, JobStatus.MERGED, reason="PR merged on the host",
                                   payload={"pr": pr.get("url", ""), "source": "merge_poll"})
                try:
                    from .sandbox.environments import cleanup_quietly
                    cleanup_quietly(service, job.id, "job done")
                except Exception:  # noqa: BLE001
                    pass
                out["merged"].append(job.id)
            elif result["state"] == "closed":
                service.store.append_event(job.id, "pr_closed", reason="PR closed without merging",
                                           payload={"url": pr.get("url", "")})
                service.require_human(job.id, AttentionReason.PR_CLOSED, "PR closed without merging",
                                      payload={"pr": pr.get("url", ""), "source": "merge_poll"})
                out["closed"].append(job.id)
            elif result.get("detail"):
                out["errors"].append({"id": job.id, "error": result["detail"]})
        except Exception as exc:  # noqa: BLE001 - one bad job never stops the pass
            log.warning("merge poll for job %s failed: %s", job.id[:10], _redact(str(exc))[:200])
            out["errors"].append({"id": job.id, "error": _redact(str(exc))[:200]})
    return out


def _release_environments(service, job_id: str) -> None:
    """Pushed jobs release their build environments unless they are kept (§6)."""
    if (service.get(job_id).metadata or {}).get("environments_kept"):
        return
    from .sandbox.environments import cleanup_quietly
    cleanup_quietly(service, job_id, "job pushed")


def run_publish(job_id: str, worker_id: str, *, lease_ttl_seconds: int = 45) -> int:
    """Publish phase (``mu.jobs.worker --phase publish``)."""
    from .lease import run_leased
    from .service import get_default_job_service

    return run_leased(
        get_default_job_service(), job_id, worker_id, phase="publish",
        lease_ttl_seconds=lease_ttl_seconds, body=lambda service, job, lease: publish_job(service, job_id),
        statuses=(JobStatus.PUBLISHING,), release_reason="publish worker exited",
    )


def main(argv=None) -> int:
    from .worker import main as worker_main
    return worker_main(argv, phase="publish")


if __name__ == "__main__":
    raise SystemExit(main())
