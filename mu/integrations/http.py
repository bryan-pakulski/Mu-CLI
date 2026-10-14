"""Shared HTTP plumbing for tracker integrations (Jira, GitHub Issues).

* :class:`TrackerError` is the base error every tracker adapter raises.
* :func:`request_with_retry` performs one HTTP call with a bounded retry on
  429 / 5xx (and connection errors for idempotent methods), honouring
  ``Retry-After``. Request headers are never echoed into error messages.
"""

from __future__ import annotations

import email.utils
import time
from typing import Any, Callable, Dict, Optional

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
MAX_WAIT_SECONDS = 30.0
IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

# Indirection so tests can avoid real sleeping.
sleep: Callable[[float], None] = time.sleep


class TrackerError(RuntimeError):
    """A tracker call failed (configuration, HTTP, or parsing)."""


def retry_delay(retry_after: Optional[str], attempt: int, *, now: Optional[float] = None) -> float:
    """Seconds to wait before retry ``attempt`` (1-based), capped at MAX_WAIT_SECONDS."""
    value = str(retry_after or "").strip()
    delay: Optional[float] = None
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(value)
                delay = when.timestamp() - (time.time() if now is None else now)
            except (TypeError, ValueError, IndexError):
                delay = None
    if delay is None:
        delay = float(2 ** (attempt - 1))
    return max(0.0, min(MAX_WAIT_SECONDS, delay))


def request_with_retry(
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    timeout: float = 20.0,
    label: str = "HTTP",
    attempts: int = MAX_ATTEMPTS,
) -> Any:
    """Send one request; retry 429 (any method) and 5xx/connection errors (idempotent only)."""
    import httpx

    method = method.upper()
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        last = attempt == attempts
        try:
            resp = httpx.request(method, url, headers=headers, params=params, json=body, timeout=timeout)
        except httpx.TransportError as exc:
            if last or method not in IDEMPOTENT:
                raise TrackerError(f"{label} {method} {url.split('?')[0]} failed: {exc.__class__.__name__}") from exc
            sleep(retry_delay(None, attempt))
            continue
        status = resp.status_code
        retryable = status == 429 or (status in RETRY_STATUSES and method in IDEMPOTENT)
        if retryable and not last:
            sleep(retry_delay(resp.headers.get("Retry-After"), attempt))
            continue
        if status >= 400:
            # Never echo request headers; the body is the server's error JSON.
            raise TrackerError(f"{label} {method} {url.split('?')[0]} failed: HTTP {status} {resp.text[:300]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise TrackerError(f"{label} {method} {url.split('?')[0]} returned non-JSON") from exc
    raise TrackerError(f"{label} {method} {url.split('?')[0]} failed")  # pragma: no cover
