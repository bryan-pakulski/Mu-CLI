"""Lint: dynamic values must be escaped before they go into Rich markup.

AST scan of the terminal code (``mu/``, ``mucli.py``, ``utils/``; never
``mu/gui/static``): an f-string that contains Rich markup tags and is passed
straight to ``<x>.print(...)``, ``show_markup(...)`` or a ``show_*`` method
must wrap every interpolated value in ``esc(...)`` / ``safe_markup(...)``
(spec §6.1 escaping rule, §8.1). Numbers formatted with a format spec
(``{n:,}``, ``{pct:.0f}``) and ``len()`` / ``int()`` results are safe.

Justified exceptions go in ``tests/wash_markup_lint_allowlist.txt`` as
``path::expression`` lines. The file is meant to stay empty.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name("wash_markup_lint_allowlist.txt")

_TAG = re.compile(r"\[/?(?:wash\.[\w.]+|bold|dim|italic|underline|reverse|strike|link=[^\]]*|/)\]|\[/\]")
_ESCAPERS = {"esc", "safe_markup", "_esc", "escape", "markup_escape"}
_SAFE_CALLS = {"len", "int", "float", "round", "abs", "sum", "min", "max", "bool"}


def _source_files():
    files = [ROOT / "mucli.py"]
    for folder in ("mu", "utils"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("mu/gui/static/"):
                continue
            files.append(path)
    return files


def _is_markup_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "print" or func.attr.startswith("show_")
    if isinstance(func, ast.Name):
        return func.id in {"show_markup", "_print"}
    return False


def _literal_text(fstring: ast.JoinedStr) -> str:
    parts = []
    for value in fstring.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append("\x00")
    return "".join(parts)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return ""


def _is_safe(value: ast.FormattedValue) -> bool:
    expr = value.value
    if value.format_spec is not None:
        spec = "".join(
            v.value for v in value.format_spec.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
        if re.search(r"[,_]?\.?\d*[dfegn%,]$", spec or "") or spec in (",",):
            return True
    if isinstance(expr, ast.Constant):
        return True
    name = _call_name(expr)
    if name in _ESCAPERS or name in _SAFE_CALLS:
        return True
    return False


def _allowlist() -> set[str]:
    if not ALLOWLIST.exists():
        return set()
    lines = ALLOWLIST.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def find_violations() -> list[str]:
    allowed = _allowlist()
    violations = []
    for path in _source_files():
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_markup_call(node):
                continue
            for arg in list(node.args) + [kw.value for kw in node.keywords if kw.arg in (None, "message", "text")]:
                fstrings = [arg] if isinstance(arg, ast.JoinedStr) else [
                    n for n in ast.walk(arg) if isinstance(n, ast.JoinedStr)
                ] if isinstance(arg, ast.BinOp) else []
                for fstring in fstrings:
                    if not _TAG.search(_literal_text(fstring)):
                        continue
                    for value in fstring.values:
                        if isinstance(value, ast.FormattedValue) and not _is_safe(value):
                            expr = ast.get_source_segment(source, value.value) or "?"
                            key = f"{rel}::{expr}"
                            if key not in allowed:
                                violations.append(f"{rel}:{value.lineno}: {{{expr}}}")
    return violations


def test_markup_fstrings_escape_dynamic_values():
    violations = find_violations()
    assert not violations, (
        "Unescaped values interpolated into Rich markup (wrap them in esc()):\n"
        + "\n".join(violations)
    )


def test_lint_catches_an_unescaped_value(tmp_path):
    sample = 'console.print(f"[wash.danger]{exc}[/wash.danger]")\n'
    tree = ast.parse(sample)
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    assert _is_markup_call(call)
    fstring = call.args[0]
    assert _TAG.search(_literal_text(fstring))
    value = next(v for v in fstring.values if isinstance(v, ast.FormattedValue))
    assert not _is_safe(value)
    safe = ast.parse('console.print(f"[wash.ok]{esc(name)} {count:,}[/]")\n')
    fs = next(n for n in ast.walk(safe) if isinstance(n, ast.JoinedStr))
    assert all(_is_safe(v) for v in fs.values if isinstance(v, ast.FormattedValue))


def test_allowlist_stays_empty():
    assert _allowlist() == set(), "justify or fix every allowlisted markup interpolation"
