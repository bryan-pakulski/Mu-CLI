"""Scan unified diffs for secrets before they leave the host.

Job sandboxes hand the agent CLI a provider credential, so a prompt-injected
agent could write it into the worktree. Publishing and stage guards call
``scan_patch`` on the job diff and refuse to continue when it reports a
finding. Only added lines are inspected: removing a leaked secret must stay
possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from mu.security.secret_paths import _SECRET_PATTERNS, is_test_fixture

# Literal values shorter than this are too likely to collide with ordinary text.
_MIN_LITERAL = 12


@dataclass(frozen=True)
class DiffSecret:
    path: str
    line: int
    label: str

    def describe(self) -> str:
        return f"{self.path}:{self.line}: {self.label}"


def _added_lines(patch: str):
    """Yield ``(path, new_line_number, text)`` for every added line."""
    path, line = "", 0
    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = target[2:] if target.startswith("b/") else target
            continue
        if raw.startswith("@@"):
            # @@ -a,b +c,d @@
            try:
                plus = raw.split("+", 1)[1].split(" ", 1)[0]
                line = int(plus.split(",", 1)[0])
            except (IndexError, ValueError):
                line = 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            yield path, line, raw[1:]
            line += 1
        elif raw.startswith(" "):
            line += 1


def scan_patch(patch: str, literals: Iterable[str] = (), *, allow_fixtures: bool = False) -> list[DiffSecret]:
    """Return secrets found on added lines of ``patch``.

    ``literals`` are exact secret values known to the host (for example the
    credential injected into the agent container); they are matched verbatim
    and never echoed back in findings.

    ``allow_fixtures``: in files under a test or fixture directory (``is_test_fixture``) the
    generic patterns (fake AWS keys, test PEM keys) do not count; ``literals`` always do.
    """
    if not patch:
        return []
    exact = [value for value in {str(v) for v in literals if v} if len(value) >= _MIN_LITERAL]
    findings: list[DiffSecret] = []
    for path, line, text in _added_lines(patch):
        for value in exact:
            if value in text:
                findings.append(DiffSecret(path, line, "injected credential"))
                break
        if allow_fixtures and is_test_fixture(path):
            continue
        for label, pattern in _SECRET_PATTERNS:
            if label == "PEM private key":
                if "PRIVATE KEY-----" in text and text.lstrip().startswith("-----BEGIN"):
                    findings.append(DiffSecret(path, line, label))
                continue
            if pattern.search(text):
                findings.append(DiffSecret(path, line, label))
    return findings


def summarize(findings: list[DiffSecret], limit: int = 5) -> str:
    shown = "; ".join(f.describe() for f in findings[:limit])
    more = f" (+{len(findings) - limit} more)" if len(findings) > limit else ""
    return f"possible secrets in the job diff: {shown}{more}"


__all__ = ["DiffSecret", "scan_patch", "summarize"]
