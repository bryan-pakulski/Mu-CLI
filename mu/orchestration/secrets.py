"""Built-in master secret store (plan §6.4, decision §12.5).

Values are encrypted with AES-256-GCM under a data key kept in ``keys/secrets.key`` (0600).
The key file is either the raw key, or — when the operator sets a passphrase — the data key
wrapped with a scrypt-derived key; then the store must be unlocked (kept in memory only) before
dispatches that need secrets. Tools declare ``secrets: [name]``; values are injected on the node
at execution time and never appear in manifests, logs, exports, audit payloads or AI-visible
results (``literals_for`` feeds the redactors).
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Dict, Iterable, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}


class SecretsLocked(RuntimeError):
    pass


class SecretStore:
    _cache: Dict[str, bytes] = {}
    _lock = threading.Lock()

    def __init__(self, store, keys_dir: str):
        self.store = store
        self.key_path = os.path.join(keys_dir, "secrets.key")

    # ------------------------------------------------------------ key handling
    def _key_doc(self) -> Optional[dict]:
        try:
            with open(self.key_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None

    def _write_doc(self, doc: dict) -> None:
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    @property
    def passphrase_protected(self) -> bool:
        doc = self._key_doc()
        return bool(doc and doc.get("wrapped"))

    @property
    def locked(self) -> bool:
        return self.passphrase_protected and self.key_path not in self._cache

    def _data_key(self, create: bool = False) -> bytes:
        doc = self._key_doc()
        if doc is None:
            if not create:
                raise SecretsLocked("no secret store yet")
            key = AESGCM.generate_key(bit_length=256)
            self._write_doc({"version": 1, "wrapped": False, "key": key.hex()})
            return key
        if not doc.get("wrapped"):
            return bytes.fromhex(doc["key"])
        with self._lock:
            key = self._cache.get(self.key_path)
        if key is None:
            raise SecretsLocked("the secret store is locked; unlock it with the passphrase")
        return key

    @staticmethod
    def _kek(passphrase: str, salt: bytes) -> bytes:
        return Scrypt(salt=salt, length=32, **_SCRYPT).derive(passphrase.encode("utf-8"))

    def set_passphrase(self, passphrase: str) -> None:
        if len(passphrase or "") < 8:
            raise ValueError("passphrase must be at least 8 characters")
        key = self._data_key(create=True)
        salt, nonce = os.urandom(16), os.urandom(12)
        wrapped = AESGCM(self._kek(passphrase, salt)).encrypt(nonce, key, b"mucli-orch-secrets")
        self._write_doc({"version": 1, "wrapped": True, "salt": salt.hex(), "nonce": nonce.hex(),
                         "key": wrapped.hex()})
        with self._lock:
            self._cache[self.key_path] = key

    def unlock(self, passphrase: str) -> bool:
        doc = self._key_doc()
        if not doc or not doc.get("wrapped"):
            return True
        try:
            key = AESGCM(self._kek(passphrase, bytes.fromhex(doc["salt"]))).decrypt(
                bytes.fromhex(doc["nonce"]), bytes.fromhex(doc["key"]), b"mucli-orch-secrets")
        except Exception:  # noqa: BLE001 - wrong passphrase
            return False
        with self._lock:
            self._cache[self.key_path] = key
        return True

    def lock(self) -> None:
        with self._lock:
            self._cache.pop(self.key_path, None)

    # ------------------------------------------------------------ values
    def put(self, name: str, value: str, description: str = "") -> None:
        if not NAME.match(str(name or "")):
            raise ValueError("secret names are lowercase snake_case")
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be a non-empty string")
        key = self._data_key(create=True)
        nonce = os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, value.encode("utf-8"), name.encode("utf-8"))
        now = self.store.now()
        with self.store.conn() as conn:
            conn.execute("INSERT INTO secrets(name, nonce, ciphertext, description, created_at, updated_at)"
                         " VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET nonce=excluded.nonce,"
                         " ciphertext=excluded.ciphertext, description=excluded.description,"
                         " updated_at=excluded.updated_at", (name, nonce, ct, description, now, now))

    def get(self, name: str) -> Optional[str]:
        with self.store.conn() as conn:
            row = conn.execute("SELECT nonce, ciphertext FROM secrets WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        key = self._data_key()
        return AESGCM(key).decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), name.encode("utf-8")).decode()

    def resolve(self, names: Iterable[str]) -> Dict[str, str]:
        """Values for a dispatch. Missing names raise ``KeyError`` (the run cannot proceed)."""
        out = {}
        for name in names:
            value = self.get(name)
            if value is None:
                raise KeyError(f"secret {name!r} is not set")
            out[name] = value
        return out

    def delete(self, name: str) -> bool:
        with self.store.conn() as conn:
            return conn.execute("DELETE FROM secrets WHERE name=?", (name,)).rowcount > 0

    def names(self) -> List[Dict[str, object]]:
        with self.store.conn() as conn:
            rows = conn.execute("SELECT name, description, created_at, updated_at FROM secrets ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def literals_for(self, names: Iterable[str]) -> List[str]:
        """Values to redact as literals; empty when locked (master redaction still runs patterns)."""
        try:
            return [v for v in (self.get(n) for n in names) if v]
        except SecretsLocked:
            return []
