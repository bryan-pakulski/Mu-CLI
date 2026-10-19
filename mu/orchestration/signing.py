"""Master keys (plan §6.3): Ed25519 signing key, test key, and the node-endpoint TLS identity.

Keys live in ``$MUCLI_HOME/orchestration/keys/`` (0700 dir, 0600 files):

* ``signing.key`` signs published tool versions, dispatches, cancels and exported bundles;
* ``test.key`` signs drafts under test, which nodes accept only when their local policy says
  ``canary: true``;
* ``tls.key`` / ``tls.pem`` the self-signed certificate nodes pin at enrolment.

Signing uses ``cryptography``; verification on nodes uses the vendored ``node.ed25519``.
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.x509.oid import NameOID

from .node import envelope as env
from .node.envelope import b64d, b64e, key_id


def _write_private(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)


def _load_or_create_ed25519(path: str) -> Ed25519PrivateKey:
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return Ed25519PrivateKey.from_private_bytes(fh.read())
    key = Ed25519PrivateKey.generate()
    _write_private(path, key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                           serialization.NoEncryption()))
    return key


def public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def verify_with(public: bytes, payload: bytes, signature_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(b64d(signature_b64), payload)
        return True
    except Exception:  # noqa: BLE001 - any failure is "not valid"
        return False


@dataclass
class TLSIdentity:
    cert_path: str
    key_path: str
    fingerprint: str  # sha256 hex of the DER certificate

    @property
    def display_fingerprint(self) -> str:
        return "SHA256:" + self.fingerprint


class KeyRing:
    def __init__(self, root: str):
        self.dir = os.path.join(root, "keys")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self._signing = _load_or_create_ed25519(os.path.join(self.dir, "signing.key"))
        self._test = _load_or_create_ed25519(os.path.join(self.dir, "test.key"))

    # ------------------------------------------------------------ public material
    @property
    def signing_public(self) -> bytes:
        return public_bytes(self._signing)

    @property
    def test_public(self) -> bytes:
        return public_bytes(self._test)

    @property
    def signing_key_id(self) -> str:
        return key_id(self.signing_public)

    def describe(self) -> Dict[str, Any]:
        return {"signing": {"key_id": self.signing_key_id, "public_key": b64e(self.signing_public)},
                "test": {"key_id": key_id(self.test_public), "public_key": b64e(self.test_public)}}

    # ------------------------------------------------------------ signing
    def sign(self, payload: bytes, *, test: bool = False) -> str:
        return b64e((self._test if test else self._signing).sign(payload))

    def sign_version(self, tool_id: str, version: int, manifest_digest: str, blobs: Mapping[str, str], *,
                     test: bool = False) -> str:
        return self.sign(env.tool_version_payload(tool_id, version, manifest_digest, blobs), test=test)

    def verify_version(self, tool_id: str, version: int, manifest_digest: str, blobs: Mapping[str, str],
                       signature: str, *, test: bool = False) -> bool:
        public = self.test_public if test else self.signing_public
        return verify_with(public, env.tool_version_payload(tool_id, version, manifest_digest, blobs), signature)

    def sign_dispatch(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        envelope = dict(envelope)
        envelope.pop("sig", None)
        envelope["sig"] = self.sign(env.dispatch_payload(envelope))
        return envelope

    def sign_cancel(self, cancel: Dict[str, Any]) -> Dict[str, Any]:
        cancel = dict(cancel)
        cancel.pop("sig", None)
        cancel["sig"] = self.sign(env.cancel_payload(cancel))
        return cancel

    def sign_update(self, order: Dict[str, Any]) -> Dict[str, Any]:
        order = dict(order)
        order.pop("sig", None)
        order["sig"] = self.sign(env.update_payload(order))
        return order

    def sign_bundle(self, bundle_digest: str) -> Dict[str, str]:
        return {"key_id": self.signing_key_id, "public_key": b64e(self.signing_public),
                "signature": self.sign(env.bundle_payload(bundle_digest))}

    # ------------------------------------------------------------ TLS
    def tls_identity(self, addresses: Iterable[str] = ()) -> TLSIdentity:
        """The node-endpoint certificate, regenerated when a bind address is not covered by it."""
        cert_path = os.path.join(self.dir, "tls.pem")
        key_path = os.path.join(self.dir, "tls.key")
        wanted = {str(a) for a in addresses if a}
        if os.path.exists(cert_path) and os.path.exists(key_path):
            with open(cert_path, "rb") as fh:
                cert = x509.load_pem_x509_certificate(fh.read())
            try:
                san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                covered = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
                covered |= set(san.get_values_for_type(x509.DNSName))
            except x509.ExtensionNotFound:
                covered = set()
            if wanted <= covered:
                return TLSIdentity(cert_path, key_path, self._fingerprint(cert))
            # Keep the key (and the operator's pin stays meaningful only if the cert is stable),
            # but add the new address. Nodes enrolled earlier must re-pin; the UI says so.
            wanted |= covered
        key = ec.generate_private_key(ec.SECP256R1())
        if os.path.exists(key_path):
            with open(key_path, "rb") as fh:
                key = serialization.load_pem_private_key(fh.read(), password=None)
        names: List[x509.GeneralName] = [x509.DNSName("mucli-master")]
        for addr in sorted(wanted | {"127.0.0.1"}):
            try:
                names.append(x509.IPAddress(ipaddress.ip_address(addr)))
            except ValueError:
                names.append(x509.DNSName(addr))
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mucli-master")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName(names), critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .sign(key, hashes.SHA256()))
        _write_private(key_path, key.private_bytes(serialization.Encoding.PEM,
                                                   serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
        with open(cert_path, "wb") as fh:
            fh.write(cert.public_bytes(serialization.Encoding.PEM))
        return TLSIdentity(cert_path, key_path, self._fingerprint(cert))

    @staticmethod
    def _fingerprint(cert: x509.Certificate) -> str:
        return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def new_nonce() -> str:
    return secrets.token_hex(16)


def now() -> float:
    return time.time()
