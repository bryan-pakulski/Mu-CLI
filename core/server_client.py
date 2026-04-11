"""HTTP client helpers so CLI and GUI can share the central MuCLI server runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator
from urllib import error, parse, request
from uuid import uuid4


class ServerClientError(RuntimeError):
    """Raised when the server cannot satisfy a client request."""


@dataclass
class MuCLIServerClient:
    base_url: str
    timeout: float = 30.0
    client_id: str = ""

    def __post_init__(self):
        normalized = str(self.base_url or "").strip().rstrip("/")
        if not normalized:
            raise ValueError("base_url is required")
        if not normalized.startswith(("http://", "https://")):
            normalized = f"http://{normalized}"
        self.base_url = normalized
        if not str(self.client_id or "").strip():
            self.client_id = f"client-{uuid4().hex}"

    def health(self) -> dict[str, Any]:
        return self._request_json("GET", "/health")

    def state(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/state")

    def capabilities(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/capabilities")

    def command(self, command: str) -> dict[str, Any]:
        return self._request_json("POST", "/api/command", {"command": command})

    def message(self, text: str) -> dict[str, Any]:
        return self._request_json("POST", "/api/message", {"text": text})

    def tasks(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/tasks")

    def task(self, task_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/tasks/{task_id}")

    def approvals(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/approvals")

    def resolve_approval(
        self, approval_id: str, decision: str, reason: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "approval_id": str(approval_id or "").strip(),
            "decision": str(decision or "").strip(),
        }
        if reason is not None:
            payload["reason"] = str(reason)
        return self._request_json("POST", "/api/approvals/resolve", payload)

    def stream_events(
        self,
        *,
        task_id: str | None = None,
        max_events: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        query = ""
        if task_id:
            query = f"?task_id={parse.quote(str(task_id).strip())}"
        url = parse.urljoin(f"{self.base_url}/", f"api/events{query}")
        req = request.Request(
            url=url,
            method="GET",
            headers={
                "Accept": "text/event-stream",
                "X-MuCLI-Client-ID": self.client_id,
            },
        )
        seen = 0
        with request.urlopen(req, timeout=self.timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                chunk = line[6:].strip()
                if not chunk:
                    continue
                try:
                    payload = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                yield payload
                seen += 1
                if max_events is not None and seen >= max(0, int(max_events)):
                    return

    def arbiter_status(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/arbiter")

    def arbiter_claim(self, *, force: bool = False) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/arbiter/claim",
            {"client_id": self.client_id, "force": bool(force)},
        )

    def arbiter_release(self) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/arbiter/release",
            {"client_id": self.client_id},
        )

    def arbiter_set_observer(self, enabled: bool) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/arbiter/observer",
            {"client_id": self.client_id, "enabled": bool(enabled)},
        )

    def _request_json(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = parse.urljoin(f"{self.base_url}/", path.lstrip("/"))
        body = None
        headers = {
            "Accept": "application/json",
            "X-MuCLI-Client-ID": self.client_id,
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url=url, method=method, headers=headers, data=body)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = str(exc)
            raise ServerClientError(
                f"{method} {path} failed with HTTP {exc.code}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise ServerClientError(
                f"{method} {path} failed: {getattr(exc, 'reason', exc)}"
            ) from exc
