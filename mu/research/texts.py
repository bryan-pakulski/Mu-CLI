"""Content-addressed store for the extracted text of read sources.

``record_claim`` checks every excerpt against the text the agent actually
read, so the text has to outlive the turn (and the run-scoped result store)
without bloating the session file. Each text is written once to
``$MUCLI_HOME/research/texts/<sha256>.md``; the session persists only the key.
A small in-process cache keeps freshly read texts available even when the
disk write fails. Extracted text is untrusted data: it is stored and returned,
never interpreted.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections import OrderedDict
from typing import Optional

MAX_TEXT_CHARS = 2_000_000
_CACHE_ENTRIES = 64

_lock = threading.Lock()
_cache: "OrderedDict[str, str]" = OrderedDict()


def _root() -> str:
    from utils.config import HISTORY_DIR

    return os.path.join(os.path.expanduser(str(HISTORY_DIR)), "research", "texts")


def text_key(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _remember(key: str, text: str) -> None:
    with _lock:
        _cache[key] = text
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_ENTRIES:
            _cache.popitem(last=False)


def put(text: str) -> Optional[str]:
    """Store ``text`` (capped) and return its key; None for empty text."""
    text = (text or "")[:MAX_TEXT_CHARS]
    if not text.strip():
        return None
    key = text_key(text)
    _remember(key, text)
    try:
        root = _root()
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, f"{key}.md")
        if not os.path.exists(path):
            fd, tmp = tempfile.mkstemp(dir=root, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except OSError:
        pass
    return key


def get(key: Optional[str]) -> Optional[str]:
    if not key or not all(c in "0123456789abcdef" for c in key):
        return None
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    try:
        with open(os.path.join(_root(), f"{key}.md"), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    _remember(key, text)
    return text
