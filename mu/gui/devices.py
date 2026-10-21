"""Paired devices: how a phone (or any non-loopback client) signs in to the GUI.

The GUI has no accounts. Loopback clients (this machine) are trusted as before. A remote client must
be *paired*: the operator starts a pairing on this machine (Settings → Devices, or ``mucli gui pair``),
which shows a 12-character code (and a QR code carrying the GUI address and the code). The device sends
``POST /api/devices/pair {code, device_name}`` and gets a device token. From then on it sends
``Authorization: Bearer <token>`` on every HTTP, SSE and WebSocket request.

* Codes live in memory only, expire after 10 minutes and are single use. They are compared in constant
  time and a wrong code never says whether a pairing is pending. Attempts are rate-limited per client
  address and overall, and wrong codes lock out with exponential backoff: after ``FREE_FAILURES`` wrong
  codes a client waits ``LOCKOUT_BASE_S`` seconds, doubling with every further wrong code up to
  ``LOCKOUT_MAX_S``; many wrong codes across all clients lock pairing out for everyone and cancel the
  pending codes (a distributed guess can't keep going). A correct code clears the client's record.
* A browser on another machine pairs on the page the gate serves to unpaired browsers: the token goes
  into an ``HttpOnly``, ``SameSite=Strict`` cookie (never readable by page scripts, never sent by other
  sites). Cookie-authenticated requests keep the browser CSRF checks; state-changing requests and
  WebSockets must also come from the same origin.
* Tokens are 32 random bytes. Only their SHA-256 is stored, in ``$MUCLI_HOME/devices.json`` (0600),
  with the device's name, creation time, last-seen time and address.
* Revoking a device takes effect on the next request; long-lived streams re-check (``still_paired``).
* Nothing here is reachable by the AI: there are no device tools, and every operator route is
  loopback-only (documentation/plans/mobile_parity.md §7a).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

CODE_TTL_S = 600.0
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I
CODE_LENGTH = 12              # shown as XXXX-XXXX-XXXX (32^12 ≈ 1.2e18 codes)
ATTEMPTS_PER_CLIENT = 5        # per window, per client address
ATTEMPTS_GLOBAL = 30           # per window, all clients (slows distributed guessing)
ATTEMPT_WINDOW_S = 60.0
FREE_FAILURES = 5              # wrong codes from one client before lockouts start
LOCKOUT_BASE_S = 30.0          # first lockout; doubles with each further wrong code
LOCKOUT_MAX_S = 3600.0
GLOBAL_FREE_FAILURES = 30      # wrong codes from all clients (within FAILURE_MEMORY_S) before a global lockout
GLOBAL_LOCKOUT_BASE_S = 60.0
FAILURE_MEMORY_S = 24 * 3600.0  # a client's wrong codes are forgotten after a quiet day
COOKIE_NAME = "mucli_device"
COOKIE_MAX_AGE_S = 400 * 24 * 3600
LAST_SEEN_EVERY_S = 60.0
LOOPBACK_HOSTS = {"localhost", "testclient"}


def is_loopback(host: str) -> bool:
    host = str(host or "").strip()
    if not host or host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def normalize_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def display_code(code: str) -> str:
    code = normalize_code(code)
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4)) if len(code) == CODE_LENGTH else code


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Device:
    id: str
    name: str
    token_hash: str
    created_at: float
    last_seen: float = 0.0
    address: str = ""

    def public(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "created_at": self.created_at,
                "last_seen": self.last_seen, "address": self.address}


@dataclass
class _Pending:
    code: str
    expires_at: float
    created_by: str = ""


@dataclass
class _Attempts:
    stamps: List[float] = field(default_factory=list)


@dataclass
class _Failures:
    count: int = 0
    last: float = 0.0
    locked_until: float = 0.0


def lockout_seconds(failures: int, *, free: int = FREE_FAILURES, base: float = LOCKOUT_BASE_S,
                    cap: float = LOCKOUT_MAX_S) -> float:
    """The lockout after ``failures`` wrong codes: none up to ``free``, then base, 2×base, 4×base … ≤ cap."""
    if failures < free:
        return 0.0
    return min(cap, base * (2 ** min(failures - free, 32)))


def _wait_text(seconds: float) -> str:
    seconds = max(1, int(seconds + 0.999))
    if seconds < 90:
        return f"{seconds} seconds"
    minutes = (seconds + 59) // 60
    return f"{minutes} minutes" if minutes < 90 else f"{(minutes + 59) // 60} hours"


class PairingError(Exception):
    """A pairing attempt failed. The message never reveals whether a code is pending."""

    def __init__(self, message: str, status: int = 401, retry_after: float = 0.0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class DeviceStore:
    def __init__(self, path: Optional[str] = None, *, clock=time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: List[_Pending] = []
        self._attempts: Dict[str, _Attempts] = {}
        self._global: _Attempts = _Attempts()
        self._failures: Dict[str, _Failures] = {}
        self._global_failures: _Failures = _Failures()
        self._devices: Dict[str, Device] = {}
        self._by_hash: Dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------ persistence
    @property
    def path(self) -> str:
        if self._path:
            return self._path
        from utils import config
        return os.path.join(os.path.expanduser(config.HISTORY_DIR), "devices.json")

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        for raw in data.get("devices") or []:
            try:
                device = Device(**{k: raw[k] for k in ("id", "name", "token_hash", "created_at")},
                                last_seen=float(raw.get("last_seen") or 0), address=str(raw.get("address") or ""))
            except (KeyError, TypeError):
                continue
            self._devices[device.id] = device
            self._by_hash[device.token_hash] = device.id

    def _save(self) -> None:
        path = self.path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"devices": [asdict(d) for d in self._devices.values()]}, fh, indent=1)
        os.replace(tmp, path)

    # ------------------------------------------------------------ pairing
    def start_pairing(self, *, created_by: str = "") -> Dict[str, Any]:
        """A new single-use code (any earlier pending code stays valid until it expires)."""
        now = self._clock()
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        with self._lock:
            self._pending = [p for p in self._pending if p.expires_at > now]
            self._pending.append(_Pending(code=code, expires_at=now + CODE_TTL_S, created_by=created_by))
        return {"code": code, "display": display_code(code), "expires_at": now + CODE_TTL_S}

    def cancel_pairings(self) -> None:
        with self._lock:
            self._pending = []

    def _throttled(self, client: str, now: float) -> bool:
        bucket = self._attempts.setdefault(client or "?", _Attempts())
        for b in (bucket, self._global):
            b.stamps = [t for t in b.stamps if now - t < ATTEMPT_WINDOW_S]
        if len(bucket.stamps) >= ATTEMPTS_PER_CLIENT or len(self._global.stamps) >= ATTEMPTS_GLOBAL:
            return True
        bucket.stamps.append(now)
        self._global.stamps.append(now)
        return False

    def lockout_remaining(self, client: str = "") -> float:
        """Seconds until ``client`` may try a code again: its wrong-code lockout, the global one, or
        the per-minute attempt limits (its own and everyone's); 0 = now."""
        now = self._clock()
        with self._lock:
            own = self._failures.get(client or "?")
            waits = [0.0, (own.locked_until if own else 0.0) - now, self._global_failures.locked_until - now]
            for bucket, limit in ((self._attempts.get(client or "?"), ATTEMPTS_PER_CLIENT), (self._global, ATTEMPTS_GLOBAL)):
                recent = sorted(t for t in (bucket.stamps if bucket else []) if now - t < ATTEMPT_WINDOW_S)
                if len(recent) >= limit:
                    waits.append(recent[-limit] + ATTEMPT_WINDOW_S - now)
            return max(waits)

    def _record_failure(self, client: str, now: float) -> float:
        """Count a wrong code; returns the lockout it starts (0 = none)."""
        record = self._failures.setdefault(client or "?", _Failures())
        if record.last and now - record.last > FAILURE_MEMORY_S:
            record.count = 0
        record.count += 1
        record.last = now
        wait = lockout_seconds(record.count)
        if wait:
            record.locked_until = now + wait
        g = self._global_failures
        if g.last and now - g.last > FAILURE_MEMORY_S:
            g.count = 0
        g.count += 1
        g.last = now
        global_wait = lockout_seconds(g.count, free=GLOBAL_FREE_FAILURES, base=GLOBAL_LOCKOUT_BASE_S)
        if global_wait:
            g.locked_until = now + global_wait
            self._pending = []      # a distributed guess can't keep working on a live code
        return max(wait, global_wait)

    def pair(self, code: str, device_name: str, *, client: str = "") -> Tuple[str, Device]:
        """Exchange a pending code for a device token; returns (token, device)."""
        now = self._clock()
        wanted = normalize_code(code).encode("utf-8")
        name = " ".join(str(device_name or "").split())[:60] or "Device"
        with self._lock:
            own = self._failures.get(client or "?")
            lockout = max((own.locked_until if own else 0.0), self._global_failures.locked_until) - now
            if lockout > 0:
                raise PairingError(f"Too many wrong codes. Try again in {_wait_text(lockout)}.", status=429,
                                   retry_after=lockout)
            if self._throttled(client, now):
                wait = max(1.0, self.lockout_remaining(client))
                raise PairingError(f"Too many attempts. Try again in {_wait_text(wait)}.", status=429, retry_after=wait)
            match = None
            for pending in self._pending:      # compare against every pending code, in constant time
                ok = hmac.compare_digest(pending.code.encode("utf-8"), wanted)
                if ok and pending.expires_at > now:
                    match = pending
            if match is None:
                wait = self._record_failure(client, now)
                if wait:
                    raise PairingError(f"That code isn't valid. Too many wrong codes: try again in {_wait_text(wait)}.",
                                       status=429, retry_after=wait)
                raise PairingError("That code isn't valid. Start a new pairing on the computer and try again.")
            self._pending.remove(match)                                   # single use
            self._failures.pop(client or "?", None)
            token = secrets.token_urlsafe(32)
            device = Device(id=secrets.token_hex(6), name=name, token_hash=_hash(token), created_at=now,
                            last_seen=now, address=str(client or ""))
            self._devices[device.id] = device
            self._by_hash[device.token_hash] = device.id
            self._save()
        return token, device

    # ------------------------------------------------------------ tokens
    def authenticate(self, token: str, *, client: str = "") -> Optional[Device]:
        if not token:
            return None
        with self._lock:
            device_id = self._by_hash.get(_hash(token))
            device = self._devices.get(device_id) if device_id else None
            if device is None:
                return None
            now = self._clock()
            if now - device.last_seen >= LAST_SEEN_EVERY_S or (client and device.address != client):
                device.last_seen = now
                device.address = client or device.address
                self._save()
            return device

    def is_active(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._devices

    def list(self) -> List[Device]:
        with self._lock:
            return sorted(self._devices.values(), key=lambda d: d.created_at)

    def revoke(self, device_id_or_name: str) -> Optional[Device]:
        with self._lock:
            device = self._devices.get(device_id_or_name) or next(
                (d for d in self._devices.values() if d.name == device_id_or_name), None)
            if device is None:
                return None
            del self._devices[device.id]
            self._by_hash.pop(device.token_hash, None)
            self._save()
            return device


# ------------------------------------------------------------ request helpers

def bearer_token(headers) -> str:
    value = str(headers.get("authorization") or "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def client_host(scope_or_request) -> str:
    client = getattr(scope_or_request, "client", None)
    if client is None and isinstance(scope_or_request, dict):
        client = scope_or_request.get("client")
    if client is None:
        return ""
    host = getattr(client, "host", None)
    if host is None and isinstance(client, (tuple, list)) and client:
        host = client[0]
    return str(host or "")


def request_device(request) -> Optional[Device]:
    """The paired device behind this request (set by the gate), or None (loopback)."""
    state = getattr(request, "state", None)
    return getattr(state, "device", None) if state is not None else None


def request_auth(request) -> str:
    """How the gate authenticated this request: "bearer" (an app's token), "cookie" (a paired
    browser) or "" (loopback)."""
    state = getattr(request, "state", None)
    return str(getattr(state, "device_auth", "") or "") if state is not None else ""


def cookie_token(headers) -> str:
    """The paired browser's token from the ``mucli_device`` cookie ('' when absent)."""
    raw = str(headers.get("cookie") or "")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME:
            return value.strip()
    return ""


def device_cookie(token: str, *, secure: bool = False) -> str:
    """Set-Cookie value for a paired browser: HttpOnly (no page script reads it), SameSite=Strict
    (no other site's request carries it)."""
    return (f"{COOKIE_NAME}={token}; Max-Age={COOKIE_MAX_AGE_S}; Path=/; HttpOnly; SameSite=Strict"
            + ("; Secure" if secure else ""))


def clear_device_cookie() -> str:
    return f"{COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"


def _same_origin(headers) -> bool:
    """A browser request from this GUI's own pages (Origin absent, or its host:port is ours)."""
    origin = str(headers.get("origin") or "")
    if not origin or origin == "null":
        return not origin
    from urllib.parse import urlsplit
    return urlsplit(origin).netloc.lower() == str(headers.get("host") or "").lower()


def still_paired(request) -> bool:
    """For long-lived streams: False once the device behind the stream has been revoked."""
    device = request_device(request)
    if device is None:
        return True
    store = getattr(request.app.state, "devices", None)
    return bool(store and store.is_active(device.id))


OPEN_PATHS = ("/healthz", "/api/devices/pair", "/api/devices/pair-browser")
# The pairing page's assets (public code: stylesheets, scripts, fonts, icons, art) load unpaired.
OPEN_PREFIXES = ("/static/",)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _pair_page(scope, path: str) -> bytes:
    """The browser pairing page (templates/pair.html), returning to ``path`` once paired."""
    from urllib.parse import quote
    app = scope.get("app")
    templates = getattr(getattr(app, "state", None), "templates", None)
    query = (scope.get("query_string") or b"").decode("latin-1")
    target = path + (f"?{query}" if query else "")
    if not target.startswith("/") or target.startswith("//"):
        target = "/"
    if templates is None:
        return b"<!doctype html><title>Pair this browser</title><p>Pair this browser from Settings - Devices on the computer.</p>"
    store = getattr(app.state, "devices", None)
    locked = store.lockout_remaining(client_host(scope)) if store is not None else 0.0
    html = templates.get_template("pair.html").render(next=quote(target, safe="/?=&%-._~"), locked_for=int(locked + 0.999),
                                                       code_length=CODE_LENGTH)
    return html.encode("utf-8")


class DeviceGate:
    """ASGI middleware: every request from a non-loopback client (HTTP, SSE and WebSocket) needs a
    paired device — an app's bearer token or a paired browser's cookie. Loopback is unchanged.
    ``/healthz``, the pairing endpoints and static assets are open; an unpaired browser asking for a
    page gets the pairing page. The device rides on ``request.state.device`` (and how it signed in
    on ``request.state.device_auth``)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        host = client_host(scope)
        path = scope.get("path") or ""
        if is_loopback(host) or path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        store = getattr(scope.get("app").state, "devices", None) if scope.get("app") is not None else None
        auth = ""
        device = None
        if store is not None:
            device = store.authenticate(bearer_token(headers), client=host)
            auth = "bearer" if device is not None else ""
            if device is None:
                device = store.authenticate(cookie_token(headers), client=host)
                auth = "cookie" if device is not None else ""
        method = str(scope.get("method") or "GET").upper()
        if device is not None and auth == "cookie" and (kind == "websocket" or method in UNSAFE_METHODS) \
                and not _same_origin(headers):
            device = None                       # another site driving the paired browser: refuse
        if device is not None:
            state = scope.setdefault("state", {})
            state["device"] = device
            state["device_auth"] = auth
            return await self.app(scope, receive, send)
        if kind == "websocket":
            await receive()                                             # websocket.connect
            await send({"type": "websocket.close", "code": 1008, "reason": "Pair this device first"})
            return
        if method in ("GET", "HEAD") and "text/html" in str(headers.get("accept") or ""):
            page = _pair_page(scope, path)
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-store"),
                                    (b"content-length", str(len(page)).encode())]})
            await send({"type": "http.response.body", "body": page if method == "GET" else b""})
            return
        body = json.dumps({"detail": "This device isn't paired. Start a pairing on the computer (Settings → Devices) and enter the code on this device.",
                           "pairing_required": True}).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
