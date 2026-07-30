"""Shared line-oriented container-shell completion and output helpers.

MUCLI_SHELL_QOL_V1
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Iterable


CWD_MARKER_PREFIX = "\x1eMUCLI_CWD:"
CWD_MARKER_SUFFIX = "\x1f"
_SHELL_SEPARATORS = frozenset(" \t\r\n;|&()<>")

@dataclass(frozen=True)
class CompletionTarget:
    start: int
    end: int
    prefix: str
    quote: str = ""


def completion_target(line: str, cursor: int | None = None) -> CompletionTarget:
    value = str(line or "")
    end = len(value) if cursor is None else max(0, min(int(cursor), len(value)))
    start = end
    while start > 0 and value[start - 1] not in _SHELL_SEPARATORS:
        start -= 1
    raw = value[start:end]
    quote = raw[:1] if raw[:1] in {"'", '"'} else ""
    prefix = raw[1:] if quote else raw
    return CompletionTarget(start=start, end=end, prefix=prefix, quote=quote)


def _escape_partial(value: str) -> str:
    out = []
    for char in value:
        if char in " \t\\'\"$`;&|()<>*?[]{}!":
            out.append("\\")
        out.append(char)
    return "".join(out)


def build_completion_response(
    *,
    line: str,
    cursor: int,
    candidates: Iterable[str],
    request_id: str = "",
) -> dict:
    target = completion_target(line, cursor)
    unique = list(dict.fromkeys(str(item or "").strip() for item in candidates if str(item or "").strip()))
    common = os.path.commonprefix(unique) if unique else ""
    replacement = ""

    if len(unique) == 1:
        candidate = unique[0]
        if target.quote:
            replacement = target.quote + candidate
            if not candidate.endswith("/"):
                replacement += target.quote + " "
        else:
            replacement = _escape_partial(candidate)
            if not candidate.endswith("/"):
                replacement += " "
    elif common and len(common) > len(target.prefix):
        replacement = (target.quote if target.quote else "") + (
            common if target.quote else _escape_partial(common)
        )

    return {
        "type": "shell_completion",
        "request_id": str(request_id or ""),
        "source": str(line or ""),
        "start": target.start,
        "end": target.end,
        "prefix": target.prefix,
        "replacement": replacement,
        "candidates": unique[:200],
    }


class CwdMarkerFilter:
    """Remove hidden PROMPT_COMMAND cwd markers from a streaming text channel."""

    def __init__(self, initial_cwd: str = ""):
        self.cwd = str(initial_cwd or "")
        self._pending = ""

    @staticmethod
    def _partial_prefix_length(value: str) -> int:
        maximum = min(len(value), len(CWD_MARKER_PREFIX) - 1)
        for length in range(maximum, 0, -1):
            if value.endswith(CWD_MARKER_PREFIX[:length]):
                return length
        return 0

    def feed(self, text: str) -> str:
        self._pending += str(text or "")
        visible: list[str] = []

        while self._pending:
            start = self._pending.find(CWD_MARKER_PREFIX)
            if start < 0:
                keep = self._partial_prefix_length(self._pending)
                if keep:
                    visible.append(self._pending[:-keep])
                    self._pending = self._pending[-keep:]
                else:
                    visible.append(self._pending)
                    self._pending = ""
                break

            if start:
                visible.append(self._pending[:start])
                self._pending = self._pending[start:]

            end = self._pending.find(CWD_MARKER_SUFFIX, len(CWD_MARKER_PREFIX))
            if end < 0:
                break

            self.cwd = self._pending[len(CWD_MARKER_PREFIX):end]
            self._pending = self._pending[end + len(CWD_MARKER_SUFFIX):]
            if self._pending.startswith("\r\n"):
                self._pending = self._pending[2:]
            elif self._pending.startswith("\n"):
                self._pending = self._pending[1:]

        return "".join(visible)

    def flush(self) -> str:
        visible = self._pending
        self._pending = ""
        return visible
