"""Node-side output redaction (first pass; the master redacts again with ``redact_secrets``).

A subset of ``mu.security.secret_paths`` patterns, vendored because the node imports nothing
outside its package, plus the literal values of the secrets injected into this execution.
"""

from __future__ import annotations

import re
from typing import Iterable, Tuple

_PATTERNS = (
    ("PEM private key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("GitLab PAT", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    ("sk-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{30,}\b")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
)
MIN_LITERAL = 4


def redact(text: str, literals: Iterable[str] = ()) -> Tuple[str, int]:
    """Replace known secret shapes and the given literal values. Returns (text, count)."""
    if not text:
        return text, 0
    count = 0
    # Longest first, so a secret that contains another is replaced whole.
    for value in sorted({str(v) for v in literals if v and len(str(v)) >= MIN_LITERAL}, key=len, reverse=True):
        n = text.count(value)
        if n:
            text = text.replace(value, "[REDACTED:secret]")
            count += n
    for label, pattern in _PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{label}]", text)
        count += n
    return text, count
