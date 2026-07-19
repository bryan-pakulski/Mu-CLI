"""CLI flag handling for file-based prompt overrides.

Consumed by ``mucli.build_session`` so ``--system-file`` and
``--mode-prompt NAME=PATH`` can be wired in without bloating
``mucli.py``. Both flags accept ``-`` to read from stdin.

These install **runtime** overrides (the same session variables ``/set``
uses: ``agentic_system_base_override`` and ``agentic_mode_prompt_<mode>``),
so they sit at the top of the resolution priority and win over
``$MUCLI_HOME/prompts/`` files and the hardcoded fallbacks. See
``mu.prompts`` for the full priority ladder.
"""

from __future__ import annotations

import os
import sys
from typing import Any


def _read_path_or_stdin(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(os.path.expanduser(path), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _parse_mode_prompt(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise ValueError(
            f"--mode-prompt expects NAME=PATH, got {spec!r}"
        )
    name, path = spec.split("=", 1)
    name = name.strip()
    path = path.strip()
    if not name or not path:
        raise ValueError(f"--mode-prompt NAME and PATH must be non-empty, got {spec!r}")
    return name, path


def apply_prompt_flags(session: Any, args: Any) -> None:
    """Apply ``--system-file`` and ``--mode-prompt`` to ``session``."""
    system_file = getattr(args, "system_file", None)
    if system_file:
        try:
            session.system_instruction = _read_path_or_stdin(system_file)
        except OSError as exc:
            raise SystemExit(f"--system-file: {exc}") from exc

    mode_prompts = getattr(args, "mode_prompt", None) or []
    from mu.prompts import known_names

    valid = set(known_names())
    for spec in mode_prompts:
        try:
            name, path = _parse_mode_prompt(spec)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if name not in valid:
            raise SystemExit(
                f"--mode-prompt: unknown name {name!r}. "
                f"Valid: {', '.join(sorted(valid))}"
            )
        try:
            content = _read_path_or_stdin(path)
        except OSError as exc:
            raise SystemExit(f"--mode-prompt {name}: {exc}") from exc
        var_key = (
            "agentic_system_base_override"
            if name == "base"
            else f"agentic_mode_prompt_{name}"
        )
        session.variables[var_key] = content


__all__ = ["apply_prompt_flags"]