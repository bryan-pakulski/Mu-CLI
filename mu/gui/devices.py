"""Paired devices: how a phone (or any non-loopback client) signs in to the GUI.

The GUI has no accounts. Loopback clients (this machine) are trusted as before. A remote client must
be *paired*: the operator starts a pairing on this machine (Settings → Devices, or ``mucli gui pair``),
which shows an 8-character code (and a QR code carrying the GUI address and the code). The device sends
``POST /api/devices/pair {code, device_name}`` and gets a device token. From then on it sends
``Authorization: Bearer <token>`` on every HTTP, SSE and WebSocket request.

* Codes live in memory only, expire after 10 minutes and are single use. They are compared in constant
  time, attempts are rate-limited per client address and overall, and a wrong code never says whether
  a pairing is pending.
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
CODE_LENGTH = 8
ATTEMPTS_PER_CLIENT = 5        # per window, per client address
ATTEMPTS_GLOBAL = 30           # per window, all clients (slows distributed guessing)
ATTEMPT_WINDOW_S = 60.0
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
    return f"{code[:4]}-{code[4:]}" if len(code) == CODE_LENGTH else code


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


class PairingError(Exception):
    """A pairing attempt failed. The message never reveals whether a code is pending."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


class DeviceStore:
    def __init__(self, path: Optional[str] = None, *, clock=time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: List[_Pending] = []
        self._attempts: Dict[str, _Attempts] = {}
        self._global: _Attempts = _Attempts()
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

    def pair(self, code: str, device_name: str, *, client: str = "") -> Tuple[str, Device]:
        """Exchange a pending code for a device token; returns (token, device)."""
        now = self._clock()
        wanted = normalize_code(code).encode("utf-8")
        name = " ".join(str(device_name or "").split())[:60] or "Device"
        with self._lock:
            if self._throttled(client, now):
                raise PairingError("Too many attempts. Wait a minute and try again.", status=429)
            match = None
            for pending in self._pending:      # compare against every pending code, in constant time
                ok = hmac.compare_digest(pending.code.encode("utf-8"), wanted)
                if ok and pending.expires_at > now:
                    match = pending
            if match is None:
                raise PairingError("That code isn't valid. Start a new pairing on the computer and try again.")
            self._pending.remove(match)                                   # single use
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
    """The paired device behind this request (set by the gate), or None (loopback / browser)."""
    state = getattr(request, "state", None)
    return getattr(state, "device", None) if state is not None else None


def still_paired(request) -> bool:
    """For long-lived streams: False once the device behind the stream has been revoked."""
    device = request_device(request)
    if device is None:
        return True
    store = getattr(request.app.state, "devices", None)
    return bool(store and store.is_active(device.id))


OPEN_PATHS = ("/healthz", "/api/devices/pair")


class DeviceGate:
    """ASGI middleware: every request from a non-loopback client (HTTP, SSE and WebSocket) needs a
    paired device's bearer token. Loopback is unchanged. ``/healthz`` and the pairing endpoint are
    open. The authenticated device rides on ``request.state.device``."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        host = client_host(scope)
        path = scope.get("path") or ""
        if is_loopback(host) or path in OPEN_PATHS:
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        store = getattr(scope.get("app").state, "devices", None) if scope.get("app") is not None else None
        device = store.authenticate(bearer_token(headers), client=host) if store is not None else None
        if device is not None:
            scope.setdefault("state", {})["device"] = device
            return await self.app(scope, receive, send)
        if kind == "websocket":
            await receive()                                             # websocket.connect
            await send({"type": "websocket.close", "code": 1008, "reason": "Pair this device first"})
            return
        body = json.dumps({"detail": "This device isn't paired. Pair it from Settings → Devices on the computer.",
                           "pairing_required": True}).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
