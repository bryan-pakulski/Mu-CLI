"""Canonical JSON and digests shared by the master and the node.

Both sides must produce byte-identical encodings for anything that is signed or hashed, so
this is the one encoder: sorted keys, no insignificant whitespace, UTF-8, no NaN.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value: Any) -> str:
    """``sha256:<hex>`` of the canonical encoding."""
    return "sha256:" + sha256_hex(canonical(value))
