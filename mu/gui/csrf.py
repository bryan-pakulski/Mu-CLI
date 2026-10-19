"""CSRF tokens for mutating GUI endpoints (orchestration plan §6.1).

The GUI has no users, so the threat is a malicious web page in the operator's browser driving
the loopback GUI. Mutations therefore require an ``X-MuCLI-CSRF`` header carrying a token the
page received from the server: a cross-site page can neither read the token (no CORS) nor send
a custom header without a preflight that fails. Tokens are ``nonce.mac`` with an HMAC key held
by this process, so each browser/page load gets its own token and no server-side table is
needed. ``check_request`` also rejects mismatched ``Origin`` headers and, against DNS
rebinding, non-local ``Host`` names (IP literals and ``localhost`` are always fine; extra names
via ``MUCLI_GUI_ALLOWED_HOSTS``).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

HEADER = "x-mucli-csrf"
_KEY = secrets.token_bytes(32)


def issue() -> str:
    nonce = secrets.token_urlsafe(16)
    mac = hmac.new(_KEY, nonce.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"{nonce}.{mac}"


def valid(token: str) -> bool:
    nonce, _, mac = str(token or "").partition(".")
    if not nonce or not mac:
        return False
    expected = hmac.new(_KEY, nonce.encode("ascii", errors="ignore"), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, mac)


def _host_ok(host_header: str) -> bool:
    host = str(host_header or "").strip()
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if not host or host in ("localhost", "testserver", "testclient"):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    allowed = {h.strip().lower() for h in os.environ.get("MUCLI_GUI_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return host.lower() in allowed


def check_host(request: Request) -> None:
    if not _host_ok(request.headers.get("host", "")):
        raise HTTPException(status_code=403, detail="unexpected Host header (set MUCLI_GUI_ALLOWED_HOSTS to allow it)")


def check_request(request: Request) -> None:
    """Dependency for mutating endpoints: Host, Origin and the CSRF header."""
    check_host(request)
    origin = request.headers.get("origin")
    if origin and origin != "null":
        if urlsplit(origin).netloc.lower() != str(request.headers.get("host", "")).lower():
            raise HTTPException(status_code=403, detail="cross-origin request refused")
    if not valid(request.headers.get(HEADER, "")):
        raise HTTPException(status_code=403, detail="missing or expired CSRF token; reload the page")
