"""Issue-tracker abstraction for job intake and writeback.

A :class:`Tracker` turns a ticket into a job payload and reports job progress
back to the ticket. Adapters:

* :class:`JiraTracker` wraps :class:`mu.integrations.jira.JiraClient`.
* :class:`GitHubTracker` talks to GitHub Issues through the REST API with
  ``GITHUB_TOKEN`` / ``GH_TOKEN``, or through ``gh api`` when no token is set.

Linear (or any other tracker) plugs in by implementing the same protocol and
registering a factory in :data:`FACTORIES`.

Ticket keys: Jira ``PROJ-12``; GitHub ``owner/repo#12``. Credentials are read
from settings/environment at call time and never stored on jobs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable
from urllib.parse import urlencode

from .http import TrackerError, request_with_retry
from .jira import (
    ISSUE_KEY_RE,
    JiraClient,
    JiraError,
    extract_acceptance_criteria,
    frame_untrusted,
    untrusted_notice,
)

GITHUB_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)$")
_GITHUB_URL_RE = re.compile(r"^https?://[^/]+/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(?:issues|pull)/(\d+)/?$")
_JIRA_BROWSE_RE = re.compile(r"/browse/([A-Z][A-Z0-9_]+-\d+)/?$")


@runtime_checkable
class Tracker(Protocol):
    name: str

    @property
    def writeback_enabled(self) -> bool: ...

    def fetch(self, key: str) -> Dict[str, Any]: ...

    def search(self, query: str, *, max_results: int = 20) -> List[Dict[str, Any]]: ...

    def key_of(self, issue: Dict[str, Any]) -> str: ...

    def to_payload(self, issue: Dict[str, Any]) -> Dict[str, Any]: ...

    def comment(self, key: str, text: str) -> bool: ...

    def transition(self, key: str, status: str) -> bool: ...

    def link_pr(self, key: str, url: str, title: str = "") -> bool: ...

    def public(self) -> Dict[str, Any]: ...


def parse_ticket_ref(text: str) -> Optional[Dict[str, str]]:
    """``PROJ-1`` / Jira browse URL -> jira; ``owner/repo#1`` / GitHub issue URL -> github."""
    value = str(text or "").strip()
    if not value:
        return None
    if ISSUE_KEY_RE.match(value.upper()) and "/" not in value:
        return {"tracker": "jira", "key": value.upper()}
    match = GITHUB_KEY_RE.match(value)
    if match:
        return {"tracker": "github", "key": value}
    match = _GITHUB_URL_RE.match(value)
    if match and "github" in value.split("/")[2]:
        owner, repo, number = match.groups()
        return {"tracker": "github", "key": f"{owner}/{repo}#{number}"}
    match = _JIRA_BROWSE_RE.search(value)
    if match:
        return {"tracker": "jira", "key": match.group(1)}
    return None


# ------------------------------------------------------------------ Jira

class JiraTracker:
    name = "jira"

    def __init__(self, client: JiraClient):
        self.client = client

    @classmethod
    def from_settings(cls) -> "JiraTracker":
        from .jira_settings import resolve_config

        config = resolve_config()
        if not (config.base_url and config.mode):
            raise JiraError("Jira is not linked")
        return cls(JiraClient(config))

    @property
    def writeback_enabled(self) -> bool:
        return bool(self.client.config.writeback)

    def fetch(self, key: str) -> Dict[str, Any]:
        return self.client.get_issue(key)

    def search(self, query: str, *, max_results: int = 20) -> List[Dict[str, Any]]:
        # Imports need the full ticket context, not the browsing columns.
        return self.client.search(query, max_results=max_results, fields=self.client.issue_fields)

    def key_of(self, issue: Dict[str, Any]) -> str:
        return str(issue.get("key") or "").strip().upper()

    def url_of(self, key: str) -> str:
        return f"{self.client.config.base_url}/browse/{key}"

    def to_payload(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        from . import jira as jira_module  # module lookup keeps the mapper patchable

        field = str(getattr(self.client.config, "acceptance_field", "") or "")
        kwargs = {"acceptance_field": field} if field else {}
        return jira_module.issue_to_payload(issue, base_url=self.client.config.base_url, **kwargs)

    def comment(self, key: str, text: str) -> bool:
        return self.client.comment(key, text)

    def transition(self, key: str, status: str) -> bool:
        return self.client.transition(key, status)

    def link_pr(self, key: str, url: str, title: str = "") -> bool:
        return self.client.link_pr(key, url, title)

    def public(self) -> Dict[str, Any]:
        return {"tracker": "jira", **self.client.config.public()}


# ------------------------------------------------------------------ GitHub

GitHubTransport = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]], Optional[Any]], Any]


def github_token(env: Optional[Dict[str, str]] = None) -> str:
    e = os.environ if env is None else env
    return str(e.get("GITHUB_TOKEN") or e.get("GH_TOKEN") or "").strip()


class GitHubClient:
    """Minimal GitHub REST client: token over HTTPS, else the ``gh`` CLI."""

    def __init__(self, *, token: str = "", api_url: str = "", transport: Optional[GitHubTransport] = None,
                 gh_runner: Optional[Callable[[List[str], Optional[str]], str]] = None, timeout: float = 20.0,
                 writeback: bool = False):
        self.token = str(token or "")
        self.api_url = (api_url or "https://api.github.com").rstrip("/")
        self._transport = transport
        self._gh_runner = gh_runner
        self.timeout = timeout
        self.writeback = bool(writeback)

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None, *, writeback: bool = False) -> "GitHubClient":
        e = os.environ if env is None else env
        return cls(token=github_token(e), api_url=str(e.get("GITHUB_API_URL") or ""), writeback=writeback)

    @property
    def mode(self) -> str:
        if self._transport is not None or self.token:
            return "token"
        return "gh" if (self._gh_runner is not None or shutil.which("gh")) else ""

    def _gh(self, method: str, path: str, params: Optional[Dict[str, Any]], body: Optional[Any]) -> Any:
        target = path.lstrip("/") + ("?" + urlencode(params) if params else "")
        argv = ["gh", "api", "-X", method, target, "-H", "Accept: application/vnd.github+json"]
        stdin = None
        if body is not None:
            argv += ["--input", "-"]
            stdin = json.dumps(body)
        if self._gh_runner is not None:
            out = self._gh_runner(argv, stdin)
        else:
            if not shutil.which("gh"):
                raise TrackerError("GitHub: set GITHUB_TOKEN/GH_TOKEN or install and log in to the gh CLI")
            try:
                proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=self.timeout + 10)
            except (OSError, subprocess.SubprocessError) as exc:
                raise TrackerError(f"GitHub {method} {path}: gh api failed: {exc.__class__.__name__}") from exc
            if proc.returncode != 0:
                raise TrackerError(f"GitHub {method} {path} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
            out = proc.stdout
        out = str(out or "").strip()
        if not out:
            return {}
        try:
            return json.loads(out)
        except ValueError as exc:
            raise TrackerError(f"GitHub {method} {path} returned non-JSON") from exc

    def request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                body: Optional[Any] = None) -> Any:
        method = method.upper()
        url = self.api_url + "/" + path.lstrip("/")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._transport is not None:
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            return self._transport(method, url, headers, params, body)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            return request_with_retry(method, url, headers=headers, params=params, body=body,
                                      timeout=self.timeout, label="GitHub")
        return self._gh(method, path, params, body)


def _split_github_key(key: str) -> tuple:
    ref = parse_ticket_ref(key)
    if not ref or ref["tracker"] != "github":
        raise TrackerError(f"invalid GitHub issue key: {key!r} (expected owner/repo#123)")
    match = GITHUB_KEY_RE.match(ref["key"])
    owner, repo, number = match.groups()
    return owner, repo, int(number)


class GitHubTracker:
    name = "github"

    def __init__(self, client: GitHubClient):
        self.client = client

    @classmethod
    def from_settings(cls) -> "GitHubTracker":
        from .jira_settings import writeback_settings

        return cls(GitHubClient.from_env(writeback=bool(writeback_settings().get("enabled"))))

    @property
    def writeback_enabled(self) -> bool:
        return bool(self.client.writeback)

    def fetch(self, key: str) -> Dict[str, Any]:
        owner, repo, number = _split_github_key(key)
        issue = dict(self.client.request("GET", f"/repos/{owner}/{repo}/issues/{number}") or {})
        if issue.get("pull_request"):
            raise TrackerError(f"{key} is a pull request, not an issue")
        try:
            comments = self.client.request("GET", f"/repos/{owner}/{repo}/issues/{number}/comments",
                                           params={"per_page": 30}) or []
        except TrackerError:
            comments = []
        issue["_key"] = f"{owner}/{repo}#{number}"
        issue["_comments"] = list(comments)[-20:] if isinstance(comments, list) else []
        return issue

    def search(self, query: str, *, max_results: int = 20) -> List[Dict[str, Any]]:
        """GitHub issue search, e.g. ``repo:acme/api is:open label:mucli``."""
        q = str(query or "").strip()
        if not q:
            raise TrackerError("GitHub search query is required")
        if "is:issue" not in q and "type:issue" not in q:
            q += " is:issue"
        cap = max(1, min(int(max_results or 20), 300))
        items: List[Dict[str, Any]] = []
        page = 1
        while len(items) < cap:
            per_page = min(100, cap - len(items))
            data = self.client.request("GET", "/search/issues",
                                       params={"q": q, "per_page": per_page, "page": page}) or {}
            batch = [i for i in (data.get("items") or []) if not i.get("pull_request")]
            items.extend(batch)
            if len(data.get("items") or []) < per_page:
                break
            page += 1
        return items[:cap]

    def key_of(self, issue: Dict[str, Any]) -> str:
        if issue.get("_key"):
            return str(issue["_key"])
        repo_url = str(issue.get("repository_url") or "")
        match = re.search(r"/repos/([^/]+)/([^/]+)$", repo_url)
        if match and issue.get("number") is not None:
            return f"{match.group(1)}/{match.group(2)}#{int(issue['number'])}"
        ref = parse_ticket_ref(str(issue.get("html_url") or ""))
        return ref["key"] if ref else ""

    def url_of(self, key: str) -> str:
        owner, repo, number = _split_github_key(key)
        return f"https://github.com/{owner}/{repo}/issues/{number}"

    def to_payload(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        key = self.key_of(issue)
        title = str(issue.get("title") or "").strip()
        body = str(issue.get("body") or "").strip()
        comments = []
        for item in issue.get("_comments") or []:
            text = str((item or {}).get("body") or "").strip()
            if text:
                comments.append({"author": str(((item or {}).get("user") or {}).get("login") or ""),
                                 "created": str((item or {}).get("created_at") or ""), "body": text[:4000]})
        labels = [str((l or {}).get("name") if isinstance(l, dict) else l) for l in (issue.get("labels") or [])][:20]
        assignees = [str((a or {}).get("login") or "") for a in (issue.get("assignees") or []) if a]
        full = body
        extra: List[str] = []
        if labels:
            extra.append("Labels: " + ", ".join(labels))
        if comments:
            extra.append("Comments:")
            extra.extend(f"- {c['author'] or 'unknown'} ({c['created'][:10]}): {c['body']}" for c in comments)
        if extra:
            full = (body + "\n\n" if body else "") + "--- GitHub issue context ---\n" + "\n".join(extra)
        url = str(issue.get("html_url") or (self.url_of(key) if key else ""))
        return {
            "title": f"{key}: {title}"[:200] if key else title[:200],
            "description": frame_untrusted(full, tracker="GitHub", key=key),
            "acceptance_criteria": extract_acceptance_criteria(body),
            "metadata": {"ticket": {
                "tracker": "github",
                "key": key,
                "url": url,
                "summary": title[:500],
                "status": str(issue.get("state") or ""),
                "labels": labels,
                "assignee": ", ".join(assignees),
                "reporter": str((issue.get("user") or {}).get("login") or ""),
                "description": body[:20000],
                "comments": comments,
                "attachments": [],
                "untrusted": True,
                "untrusted_notice": untrusted_notice("GitHub", key),
            }},
        }

    def comment(self, key: str, text: str) -> bool:
        if not self.writeback_enabled:
            return False
        owner, repo, number = _split_github_key(key)
        self.client.request("POST", f"/repos/{owner}/{repo}/issues/{number}/comments",
                            body={"body": str(text or "")[:60000]})
        return True

    def transition(self, key: str, status: str) -> bool:
        """``close`` / ``reopen`` / ``label:<name>`` / ``unlabel:<name>`` (comma-separated)."""
        if not self.writeback_enabled:
            return False
        owner, repo, number = _split_github_key(key)
        did = False
        for action in [a.strip() for a in str(status or "").split(",") if a.strip()]:
            lowered = action.lower()
            if lowered in {"close", "closed"}:
                self.client.request("PATCH", f"/repos/{owner}/{repo}/issues/{number}",
                                    body={"state": "closed", "state_reason": "completed"})
            elif lowered in {"reopen", "open"}:
                self.client.request("PATCH", f"/repos/{owner}/{repo}/issues/{number}", body={"state": "open"})
            elif lowered.startswith("label:") and action[6:].strip():
                self.client.request("POST", f"/repos/{owner}/{repo}/issues/{number}/labels",
                                    body={"labels": [action[6:].strip()]})
            elif lowered.startswith("unlabel:") and action[8:].strip():
                from urllib.parse import quote

                self.client.request("DELETE", f"/repos/{owner}/{repo}/issues/{number}/labels/"
                                    + quote(action[8:].strip(), safe=""))
            else:
                raise TrackerError(f"unsupported GitHub action {action!r} (close, reopen, label:X, unlabel:X)")
            did = True
        return did

    def link_pr(self, key: str, url: str, title: str = "") -> bool:
        url = str(url or "").strip()
        if not url.startswith(("https://", "http://")):
            return False
        return self.comment(key, f"{title or 'Pull request'}: {url}")

    def public(self) -> Dict[str, Any]:
        return {"tracker": "github", "mode": self.client.mode, "api_url": self.client.api_url,
                "writeback": self.writeback_enabled, "configured": bool(self.client.mode)}


FACTORIES: Dict[str, Callable[[], Tracker]] = {
    "jira": JiraTracker.from_settings,
    "github": GitHubTracker.from_settings,
}


def get_tracker(name: str) -> Tracker:
    factory = FACTORIES.get(str(name or "").strip().lower())
    if factory is None:
        raise TrackerError(f"unknown tracker {name!r} (known: {', '.join(sorted(FACTORIES))})")
    return factory()
