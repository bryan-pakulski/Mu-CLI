"""Jira intake for durable jobs.

Credentials come from the environment only (never from job payloads, never
persisted on a job):

* ``JIRA_BASE_URL``  - e.g. https://acme.atlassian.net or https://jira.corp
* Cloud:  ``JIRA_EMAIL`` + ``JIRA_API_TOKEN``   -> HTTP basic auth
* Data Center / Server: ``JIRA_PAT``             -> ``Authorization: Bearer``
* ``JIRA_WRITEBACK=1`` opts into commenting back on issues (default off).

The client is read-only unless writeback is explicitly enabled.
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
_AC_HEADING_RE = re.compile(r"^\s*(?:h\d\.\s*|#+\s*)?acceptance\s+criteria\s*:?\s*$", re.I)
_BULLET_RE = re.compile(r"^\s*(?:[-*#]+|\d+[.)])\s+(.*\S)\s*$")
_FIELDS = "summary,description,status,issuetype,priority,labels,assignee"
# Single-issue fetch (job import) pulls the full ticket context for planning.
_ISSUE_FIELDS = _FIELDS + ",comment,parent,subtasks,issuelinks,components,fixVersions,reporter,environment"


class JiraError(RuntimeError):
    pass


@dataclass
class JiraConfig:
    base_url: str = ""
    email: str = ""
    api_token: str = ""
    pat: str = ""
    writeback: bool = False
    timeout: float = 20.0

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "JiraConfig":
        e = os.environ if env is None else env
        return cls(
            base_url=str(e.get("JIRA_BASE_URL", "")).strip().rstrip("/"),
            email=str(e.get("JIRA_EMAIL", "")).strip(),
            api_token=str(e.get("JIRA_API_TOKEN", "")).strip(),
            pat=str(e.get("JIRA_PAT", "")).strip(),
            writeback=str(e.get("JIRA_WRITEBACK", "")).strip().lower() in {"1", "true", "yes", "on"},
        )

    @property
    def mode(self) -> str:
        if self.pat:
            return "bearer"
        if self.email and self.api_token:
            return "basic"
        return ""

    def validate(self) -> None:
        if not self.base_url:
            raise JiraError("JIRA_BASE_URL is not set")
        if not self.base_url.startswith(("https://", "http://")):
            raise JiraError("JIRA_BASE_URL must start with https://")
        if not self.mode:
            raise JiraError("Set JIRA_EMAIL + JIRA_API_TOKEN (Cloud) or JIRA_PAT (Data Center)")

    def auth_header(self) -> Dict[str, str]:
        if self.mode == "bearer":
            return {"Authorization": f"Bearer {self.pat}"}
        raw = f"{self.email}:{self.api_token}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def public(self) -> Dict[str, Any]:
        """Non-secret view, safe for API responses and logs."""
        return {"base_url": self.base_url, "mode": self.mode, "writeback": self.writeback,
                "configured": bool(self.base_url and self.mode)}


Transport = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Any]


def _httpx_transport(timeout: float) -> Transport:
    def _send(method, url, headers, params, body):
        import httpx

        resp = httpx.request(method, url, headers=headers, params=params, json=body, timeout=timeout)
        if resp.status_code >= 400:
            # Never echo request headers; body is Jira's error JSON.
            raise JiraError(f"Jira {method} {url.split('?')[0]} failed: HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json() if resp.content else {}

    return _send


class JiraClient:
    def __init__(self, config: JiraConfig, transport: Optional[Transport] = None):
        config.validate()
        self.config = config
        self._send = transport or _httpx_transport(config.timeout)
        self.api = "/rest/api/3" if config.mode == "basic" else "/rest/api/2"

    def _request(self, method: str, path: str, *, params=None, body=None) -> Any:
        headers = {"Accept": "application/json", **self.config.auth_header()}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return self._send(method, f"{self.config.base_url}{self.api}{path}", headers, params, body)

    def get_issue(self, key: str) -> Dict[str, Any]:
        key = str(key or "").strip().upper()
        if not ISSUE_KEY_RE.match(key):
            raise JiraError(f"invalid issue key: {key!r}")
        return self._request("GET", f"/issue/{key}", params={"fields": _ISSUE_FIELDS})

    def search(self, jql: str, *, max_results: int = 20, fields: str = _FIELDS) -> List[Dict[str, Any]]:
        """JQL search, paginated up to ``max_results`` (cap 500).

        Jira Cloud removed ``GET /rest/api/3/search`` (HTTP 410) in favour of
        ``/search/jql`` with token paging; Data Center keeps ``/search`` +
        ``startAt``.
        """
        jql = str(jql or "").strip()
        if not jql:
            raise JiraError("JQL is required")
        cap = _clamp(max_results, 1, 500)
        issues: List[Dict[str, Any]] = []
        if self.config.mode == "basic":
            token = ""
            while len(issues) < cap:
                params: Dict[str, Any] = {"jql": jql, "fields": fields,
                                          "maxResults": min(100, cap - len(issues))}
                if token:
                    params["nextPageToken"] = token
                data = self._request("GET", "/search/jql", params=params) or {}
                page = list(data.get("issues") or [])
                issues.extend(page)
                token = str(data.get("nextPageToken") or "")
                if not page or not token or data.get("isLast"):
                    break
        else:
            while len(issues) < cap:
                want = min(100, cap - len(issues))
                data = self._request("GET", "/search", params={
                    "jql": jql, "fields": fields, "startAt": len(issues), "maxResults": want,
                }) or {}
                page = list(data.get("issues") or [])
                issues.extend(page)
                total = data.get("total")
                if not page or len(page) < want or (isinstance(total, int) and len(issues) >= total):
                    break
        return issues[:cap]

    def comment(self, key: str, text: str) -> bool:
        """Writeback; a no-op unless JIRA_WRITEBACK is enabled."""
        if not self.config.writeback:
            return False
        body: Dict[str, Any]
        if self.api.endswith("/3"):
            body = {"body": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": text[:30000]}]}]}}
        else:
            body = {"body": text[:30000]}
        self._request("POST", f"/issue/{key}/comment", body=body)
        return True

    # ------------------------------------------------------------ browsing
    # Boards and sprints live on the Agile API (same auth, different root).

    def _agile(self, path: str, *, params=None) -> Any:
        headers = {"Accept": "application/json", **self.config.auth_header()}
        return self._send("GET", f"{self.config.base_url}/rest/agile/1.0{path}", headers, params, None)

    def myself(self) -> Dict[str, Any]:
        """Identity behind the configured credentials (connection test)."""
        data = self._request("GET", "/myself") or {}
        return {
            "account_id": str(data.get("accountId") or data.get("key") or data.get("name") or ""),
            "display_name": str(data.get("displayName") or ""),
            "email": str(data.get("emailAddress") or ""),
        }

    def _agile_pages(self, path: str, key: str, *, params=None, cap: int = 500) -> List[Dict[str, Any]]:
        """Follow Agile API ``startAt`` paging (pages are capped at 50 server-side)."""
        cap = _clamp(cap, 1, 1000)
        items: List[Dict[str, Any]] = []
        while len(items) < cap:
            page_params = dict(params or {})
            page_params["startAt"] = len(items)
            want = min(50, cap - len(items))
            page_params["maxResults"] = want
            data = self._agile(path, params=page_params) or {}
            page = list(data.get(key) or [])
            items.extend(page)
            total = data.get("total")
            if (not page or data.get("isLast") is True or len(page) < want
                    or (isinstance(total, int) and len(items) >= total)):
                break
        return items[:cap]

    def list_boards(self, *, query: str = "", max_results: int = 500) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if str(query or "").strip():
            params["name"] = str(query).strip()
        boards = []
        for item in self._agile_pages("/board", "values", params=params, cap=max_results):
            if item.get("id") is None:
                continue
            location = item.get("location") or {}
            boards.append({
                "id": int(item["id"]),
                "name": str(item.get("name") or ""),
                "type": str(item.get("type") or ""),
                "project": str(location.get("projectKey") or ""),
                "project_name": str(location.get("projectName") or location.get("displayName") or ""),
            })
        return boards

    def my_project_keys(self, *, max_results: int = 100) -> List[str]:
        """Projects of open issues assigned to me, most frequent first (board suggestions)."""
        try:
            issues = self.search(
                "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC",
                max_results=max_results, fields="project",
            )
        except JiraError:
            return []
        counts: Dict[str, int] = {}
        for issue in issues:
            key = str(((issue.get("fields") or {}).get("project") or {}).get("key") or "")
            if key:
                counts[key] = counts.get(key, 0) + 1
        return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def list_sprints(self, board_id: int, *, state: str = "active,future") -> List[Dict[str, Any]]:
        try:
            data = self._agile(
                f"/board/{int(board_id)}/sprint",
                params={"state": state or "active,future", "maxResults": 50},
            ) or {}
        except JiraError as exc:
            # Kanban boards have no sprints; Jira answers 400 for them.
            if "sprint" in str(exc).lower():
                return []
            raise
        return [
            {
                "id": int(item["id"]),
                "name": str(item.get("name") or ""),
                "state": str(item.get("state") or ""),
                "start_date": str(item.get("startDate") or ""),
                "end_date": str(item.get("endDate") or ""),
                "goal": str(item.get("goal") or "")[:500],
            }
            for item in data.get("values") or []
            if item.get("id") is not None
        ]

    def board_issues(
        self,
        board_id: int,
        *,
        sprint_id: Optional[int] = None,
        jql: str = "",
        max_results: int = 300,
        backlog: bool = False,
    ) -> List[Dict[str, Any]]:
        """Issues for a sprint, a board's backlog, or the whole board — all pages.

        The Agile API returns at most 50 per page; without paging, a sprint
        full of Done tickets hides every To Do / Blocked one.
        """
        params: Dict[str, Any] = {"fields": _FIELDS}
        if str(jql or "").strip():
            params["jql"] = str(jql).strip()
        if sprint_id:
            path = f"/sprint/{int(sprint_id)}/issue"
        elif backlog:
            path = f"/board/{int(board_id)}/backlog"
        else:
            path = f"/board/{int(board_id)}/issue"
        return self._agile_pages(path, "issues", params=params, cap=_clamp(max_results, 1, 1000))


def _clamp(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = high
    return max(low, min(number, high))


# ------------------------------------------------------------------ mapping

def adf_to_text(node: Any) -> str:
    """Flatten Atlassian Document Format (Cloud v3) or pass through wiki text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text") or "")
    if kind == "hardBreak":
        return "\n"
    inner = adf_to_text(node.get("content") or [])
    if kind == "listItem":
        return "- " + inner.strip() + "\n"
    if kind in {"paragraph", "heading", "codeBlock", "blockquote"}:
        return inner.rstrip() + "\n"
    return inner


def extract_acceptance_criteria(description: str) -> List[str]:
    criteria: List[str] = []
    in_section = False
    for line in str(description or "").splitlines():
        if _AC_HEADING_RE.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        match = _BULLET_RE.match(line)
        if match:
            criteria.append(match.group(1).strip())
        elif line.strip() and criteria:
            break  # next prose block ends the section
    return criteria[:30]


def _ref(item: Any) -> Dict[str, str]:
    item = dict(item or {})
    fields = dict(item.get("fields") or {})
    return {
        "key": str(item.get("key") or ""),
        "summary": str(fields.get("summary") or "")[:300],
        "status": str((fields.get("status") or {}).get("name") or ""),
    }


def issue_context(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Structured ticket context beyond the description (comments, links, ...)."""
    comments = []
    raw_comments = (fields.get("comment") or {}).get("comments") if isinstance(fields.get("comment"), dict) else None
    for item in list(raw_comments or [])[-20:]:
        body = adf_to_text((item or {}).get("body")).strip()
        if body:
            comments.append({
                "author": str(((item or {}).get("author") or {}).get("displayName") or ""),
                "created": str((item or {}).get("created") or ""),
                "body": body[:4000],
            })
    links = []
    for link in list(fields.get("issuelinks") or [])[:30]:
        link = dict(link or {})
        kind = dict(link.get("type") or {})
        if link.get("outwardIssue"):
            links.append({"relation": str(kind.get("outward") or "relates to"), **_ref(link["outwardIssue"])})
        elif link.get("inwardIssue"):
            links.append({"relation": str(kind.get("inward") or "relates to"), **_ref(link["inwardIssue"])})
    parent = _ref(fields["parent"]) if isinstance(fields.get("parent"), dict) else {}
    return {
        "parent": parent if parent.get("key") else {},
        "subtasks": [_ref(s) for s in list(fields.get("subtasks") or [])[:30]],
        "links": links,
        "components": [str((c or {}).get("name") or "") for c in (fields.get("components") or []) if c][:20],
        "fix_versions": [str((v or {}).get("name") or "") for v in (fields.get("fixVersions") or []) if v][:20],
        "reporter": str((fields.get("reporter") or {}).get("displayName") or ""),
        "environment": adf_to_text(fields.get("environment")).strip()[:2000],
        "comments": comments,
    }


def context_text(context: Dict[str, Any]) -> str:
    """Render ticket context as plain text appended to the job description."""
    lines: List[str] = []
    if context.get("parent"):
        p = context["parent"]
        lines.append(f"Parent: {p['key']} {p['summary']}".rstrip())
    if context.get("components"):
        lines.append("Components: " + ", ".join(context["components"]))
    if context.get("fix_versions"):
        lines.append("Fix versions: " + ", ".join(context["fix_versions"]))
    if context.get("environment"):
        lines.extend(["Environment:", context["environment"]])
    if context.get("subtasks"):
        lines.append("Subtasks:")
        lines.extend(f"- {s['key']} [{s['status']}] {s['summary']}" for s in context["subtasks"])
    if context.get("links"):
        lines.append("Linked issues:")
        lines.extend(f"- {l['relation']} {l['key']} [{l['status']}] {l['summary']}" for l in context["links"])
    if context.get("comments"):
        lines.append("Comments:")
        for c in context["comments"]:
            lines.append(f"- {c['author'] or 'unknown'} ({c['created'][:10]}): {c['body']}")
    return "\n".join(lines)


def issue_to_payload(issue: Dict[str, Any], *, base_url: str) -> Dict[str, Any]:
    fields = dict(issue.get("fields") or {})
    key = str(issue.get("key") or "").strip()
    summary = str(fields.get("summary") or "").strip()
    description = adf_to_text(fields.get("description")).strip()
    context = issue_context(fields)
    extra = context_text(context)
    full = description
    if extra:
        full = (description + "\n\n" if description else "") + "--- Jira ticket context ---\n" + extra
    return {
        "title": f"{key}: {summary}"[:200] if key else summary[:200],
        "description": full[:20000],
        "acceptance_criteria": extract_acceptance_criteria(description),
        "metadata": {"jira": {
            "key": key,
            "url": f"{base_url}/browse/{key}" if key else "",
            "summary": summary[:500],
            "status": str((fields.get("status") or {}).get("name") or ""),
            "issue_type": str((fields.get("issuetype") or {}).get("name") or ""),
            "priority": str((fields.get("priority") or {}).get("name") or ""),
            "labels": [str(v) for v in (fields.get("labels") or [])][:20],
            "assignee": str((fields.get("assignee") or {}).get("displayName") or ""),
            "description": description[:20000],
            **context,
        }},
    }


def _existing_keys(service) -> Dict[str, str]:
    from mu.jobs.models import TERMINAL_STATUSES

    found: Dict[str, str] = {}
    for job in service.list(limit=1000):
        key = str(((job.metadata or {}).get("jira") or {}).get("key") or "")
        if key and job.status not in TERMINAL_STATUSES:
            found[key] = job.id
    return found


def import_issues(
    service,
    client: JiraClient,
    *,
    keys: Iterable[str] = (),
    jql: str = "",
    defaults: Optional[Dict[str, Any]] = None,
    max_results: int = 20,
) -> Dict[str, Any]:
    """Create one durable job per issue. Skips issues that already have a live job."""
    issues: List[Dict[str, Any]] = [client.get_issue(k) for k in keys if str(k).strip()]
    if jql:
        issues.extend(client.search(jql, max_results=max_results))
    existing = _existing_keys(service)
    created, skipped, errors = [], [], []
    for issue in issues:
        key = str(issue.get("key") or "")
        if key in existing:
            skipped.append({"key": key, "job_id": existing[key], "reason": "active job exists"})
            continue
        payload = {**dict(defaults or {}), **issue_to_payload(issue, base_url=client.config.base_url)}
        payload["execution"] = dict((defaults or {}).get("execution") or {})
        try:
            job = service.create_from_payload(payload)
        except (TypeError, ValueError) as exc:
            errors.append({"key": key, "error": str(exc)})
            continue
        existing[key] = job.id
        created.append({"key": key, "job_id": job.id, "title": job.title})
        try:
            client.comment(key, f"MuCLI durable job {job.id[:10]} created for this issue.")
        except JiraError as exc:
            errors.append({"key": key, "error": f"writeback failed: {exc}"})
    return {"created": created, "skipped": skipped, "errors": errors, "jira": client.config.public()}


# ------------------------------------------------------------------ browsing view

def linked_jobs(service, *, limit: int = 1000) -> Dict[str, Dict[str, Any]]:
    """Map Jira key -> most relevant linked job (live beats terminal, then newest)."""
    from mu.jobs.models import TERMINAL_STATUSES

    found: Dict[str, Dict[str, Any]] = {}
    for job in service.list(limit=limit):
        key = str(((job.metadata or {}).get("jira") or {}).get("key") or "").strip()
        if not key:
            continue
        live = job.status not in TERMINAL_STATUSES
        entry = {
            "job_id": job.id,
            "title": job.title,
            "status": job.status.value,
            "attention_reason": getattr(job.attention_reason, "value", "") or "",
            "live": live,
            "updated_at": float(job.updated_at or 0.0),
        }
        current = found.get(key)
        if current is None or (live, entry["updated_at"]) > (current["live"], current["updated_at"]):
            found[key] = entry
    return found


def ticket_summary(issue: Dict[str, Any], *, base_url: str,
                   linked: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Compact, secret-free ticket row for the GUI, with its linked job (if any)."""
    fields = dict(issue.get("fields") or {})
    key = str(issue.get("key") or "").strip()
    status = fields.get("status") or {}
    category = (status.get("statusCategory") or {}).get("key") or ""
    assignee = fields.get("assignee") or {}
    return {
        "key": key,
        "summary": str(fields.get("summary") or "")[:300],
        "url": f"{base_url}/browse/{key}" if key else "",
        "status": str(status.get("name") or ""),
        "status_category": str(category),
        "issue_type": str((fields.get("issuetype") or {}).get("name") or ""),
        "priority": str((fields.get("priority") or {}).get("name") or ""),
        "assignee": str(assignee.get("displayName") or ""),
        "labels": [str(v) for v in (fields.get("labels") or [])][:10],
        "job": (linked or {}).get(key),
    }


# ------------------------------------------------------------------ ticket filters

_ORDER_BY_RE = re.compile(r"\s+ORDER\s+BY\s+.*$", re.I | re.S)


def _jql_quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_ticket_jql(jql: str = "", *, mine: bool = False, include_done: bool = False,
                     statuses: Iterable[str] = ()) -> str:
    """Combine a user JQL clause with browser filters.

    Done/closed work (status category Done) is excluded unless
    ``include_done``. A trailing ``ORDER BY`` in the user clause is kept last.
    """
    text = str(jql or "").strip()
    order = ""
    match = _ORDER_BY_RE.search(" " + text)
    if match:
        order = match.group(0).strip()
        text = _ORDER_BY_RE.sub("", " " + text).strip()
    clauses = []
    if text:
        clauses.append(f"({text})")
    if mine:
        clauses.append("assignee = currentUser()")
    names = [str(s).strip() for s in statuses if str(s).strip()]
    if names:
        clauses.append("status in (" + ", ".join(_jql_quote(s) for s in names[:30]) + ")")
    elif not include_done:
        clauses.append("statusCategory != Done")
    query = " AND ".join(clauses)
    if order:
        query = f"{query} {order}".strip()
    return query


_CATEGORY_ORDER = {"new": 0, "indeterminate": 1, "done": 2}


def status_counts(tickets: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Distinct statuses in a ticket list (workflow order: to do -> in progress -> done)."""
    counts: Dict[str, Dict[str, Any]] = {}
    for ticket in tickets:
        name = str(ticket.get("status") or "")
        if not name:
            continue
        entry = counts.setdefault(name, {"name": name, "category": str(ticket.get("status_category") or ""), "count": 0})
        entry["count"] += 1
    return sorted(counts.values(), key=lambda e: (_CATEGORY_ORDER.get(e["category"], 1), e["name"].lower()))
