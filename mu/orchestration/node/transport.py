"""HTTPS to the master with certificate pinning (plan §6.2), standard library only.

The node stores the master's certificate at enrolment and trusts exactly that certificate:
the TLS context uses it as the only CA, and after the handshake the peer certificate's SHA-256
fingerprint must equal the pinned one. Hostname checks are off because the pin is stronger
(and lets nodes dial an overlay or tunnel address). Plain ``http://`` is accepted only for
loopback URLs (tests and SSH-tunnel layouts terminate TLS elsewhere).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import socket
import ssl
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit


class TransportError(RuntimeError):
    pass


class AuthRejected(TransportError):
    """The master refused our credential (revoked or unknown)."""


def normalize_fingerprint(text: str) -> str:
    value = str(text or "").strip()
    if value.upper().startswith("SHA256:"):
        value = value[7:]
    return value.replace(":", "").lower()


def cert_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def fetch_certificate(url: str, timeout: float = 10.0) -> Tuple[str, bytes]:
    """The master's certificate (PEM, DER) as presented, without trusting it yet."""
    parts = urlsplit(url)
    host, port = parts.hostname or "", parts.port or 443
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    return ssl.DER_cert_to_PEM_cert(der), der


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class MasterClient:
    def __init__(self, url: str, *, cafile: Optional[str] = None, fingerprint: str = "",
                 node_id: str = "", credential: str = "", timeout: float = 40.0):
        parts = urlsplit(url.rstrip("/"))
        if parts.scheme not in ("https", "http"):
            raise TransportError("master URL must be https://")
        self.scheme = parts.scheme
        self.host = parts.hostname or ""
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        if self.scheme == "http" and not _loopback(self.host):
            raise TransportError("plain http is only allowed to a loopback address")
        self.base = parts.path.rstrip("/")
        self.cafile = cafile
        self.fingerprint = normalize_fingerprint(fingerprint)
        self.node_id = node_id
        self.credential = credential
        self.timeout = timeout

    def _connect(self, timeout: float) -> http.client.HTTPConnection:
        if self.scheme == "http":
            return http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        if not self.cafile or not self.fingerprint:
            raise TransportError("TLS pin is missing (enrol first)")
        ctx = ssl.create_default_context(cafile=self.cafile)
        ctx.check_hostname = False
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=ctx)
        conn.connect()
        der = conn.sock.getpeercert(binary_form=True) if conn.sock else None
        if not der or not hmac.compare_digest(cert_fingerprint(der), self.fingerprint):
            conn.close()
            raise TransportError("master certificate does not match the pinned fingerprint")
        return conn

    def request(self, method: str, path: str, body: Any = None, *, timeout: Optional[float] = None,
                raw: bool = False) -> Any:
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "mucli-node"}
        if self.node_id and self.credential:
            headers["X-MuCLI-Node"] = self.node_id
            headers["X-MuCLI-Node-Token"] = self.credential
        payload = None if body is None else json.dumps(body).encode("utf-8")
        try:
            conn = self._connect(timeout or self.timeout)
            try:
                conn.request(method, self.base + path, body=payload, headers=headers)
                resp = conn.getresponse()
                data = resp.read(64 * 1024 * 1024)
            finally:
                conn.close()
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError(f"{method} {path}: {exc}") from exc
        if resp.status in (401, 403):
            raise AuthRejected(f"master refused the node credential ({resp.status})")
        if resp.status >= 400:
            raise TransportError(f"{method} {path}: HTTP {resp.status} {data[:300]!r}")
        if raw:
            return data
        return json.loads(data.decode("utf-8")) if data else {}

    def post(self, path: str, body: Dict[str, Any], **kw) -> Any:
        return self.request("POST", path, body, **kw)

    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)
