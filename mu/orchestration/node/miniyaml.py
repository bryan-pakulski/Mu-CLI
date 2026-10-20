"""A deliberately small YAML subset for node-local files (the node has no PyYAML).

Supported: block mappings and sequences by indentation, ``key: value`` scalars, flow lists
``[a, "b"]`` and flow maps ``{k: v}`` of scalars, ``#`` comments, single/double quoted strings,
ints, floats, booleans and null. Anything else raises ``ValueError`` (with a line number) rather
than being guessed at: a policy file that does not parse must never be read as permissive.
JSON documents are accepted as-is.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Tuple

_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def _strip_comment(line: str) -> str:
    out, quote = [], ""
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_flow(text: str, lineno: int) -> List[str]:
    parts, depth, quote, cur = [], 0, "", []
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if quote or depth:
        raise ValueError(f"line {lineno}: unbalanced flow collection")
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def scalar(text: str, lineno: int = 0) -> Any:
    text = text.strip()
    if text == "":
        return None
    if text[0] in ("'", '"'):
        if len(text) < 2 or text[-1] != text[0]:
            raise ValueError(f"line {lineno}: unterminated string")
        body = text[1:-1]
        if text[0] == '"':
            try:
                return json.loads(text)
            except ValueError as exc:
                raise ValueError(f"line {lineno}: bad string ({exc})") from exc
        return body.replace("''", "'")
    if text.startswith("["):
        if not text.endswith("]"):
            raise ValueError(f"line {lineno}: unterminated list")
        return [scalar(p, lineno) for p in _split_flow(text[1:-1], lineno)]
    if text.startswith("{"):
        if not text.endswith("}"):
            raise ValueError(f"line {lineno}: unterminated map")
        out = {}
        for part in _split_flow(text[1:-1], lineno):
            key, sep, value = part.partition(":")
            if not sep:
                raise ValueError(f"line {lineno}: expected key: value in flow map")
            out[str(scalar(key, lineno))] = scalar(value, lineno)
        return out
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if _INT.match(text):
        return int(text)
    if _FLOAT.match(text):
        return float(text)
    if text.startswith(("&", "*", "!", "|", ">")):
        raise ValueError(f"line {lineno}: unsupported YAML feature {text[0]!r}")
    return text


def _split_key(content: str, lineno: int) -> Tuple[str, str]:
    quote = ""
    for i, ch in enumerate(content):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == ":" and (i + 1 == len(content) or content[i + 1] in " \t"):
            return str(scalar(content[:i], lineno)), content[i + 1:].strip()
    raise ValueError(f"line {lineno}: expected 'key: value'")


def loads(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except ValueError:
            pass
    lines: List[Tuple[int, int, str]] = []
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ValueError(f"line {n}: tabs are not allowed for indentation")
        clean = _strip_comment(raw)
        if not clean.strip() or clean.strip() in ("---", "..."):
            continue
        lines.append((n, len(clean) - len(clean.lstrip(" ")), clean.strip()))
    if not lines:
        return {}
    value, pos = _block(lines, 0, lines[0][1])
    if pos != len(lines):
        raise ValueError(f"line {lines[pos][0]}: unexpected indentation")
    return value


def _block(lines, pos: int, indent: int):
    if lines[pos][2].startswith("- ") or lines[pos][2] == "-":
        return _seq(lines, pos, indent)
    return _map(lines, pos, indent)


def _map(lines, pos: int, indent: int):
    out = {}
    while pos < len(lines):
        n, ind, content = lines[pos]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError(f"line {n}: unexpected indentation")
        if content.startswith("- "):
            raise ValueError(f"line {n}: list item where a key was expected")
        key, rest = _split_key(content, n)
        pos += 1
        if rest:
            out[key] = scalar(rest, n)
        elif pos < len(lines) and (lines[pos][1] > indent or
                                   (lines[pos][1] == indent and lines[pos][2].startswith("- "))):
            out[key], pos = _block(lines, pos, lines[pos][1])
        else:
            out[key] = None
    return out, pos


def _seq(lines, pos: int, indent: int):
    out = []
    while pos < len(lines):
        n, ind, content = lines[pos]
        if ind < indent or not (content.startswith("- ") or content == "-"):
            break
        if ind > indent:
            raise ValueError(f"line {n}: unexpected indentation")
        rest = content[1:].strip()
        pos += 1
        if not rest:
            if pos < len(lines) and lines[pos][1] > indent:
                item, pos = _block(lines, pos, lines[pos][1])
                out.append(item)
            else:
                out.append(None)
            continue
        is_mapping = False
        try:
            _split_key(rest, n)
            is_mapping = not rest.startswith(("'", '"', "[", "{"))
        except ValueError:
            pass
        if is_mapping:
            # "- key: value" starts a mapping whose further keys sit at indent + 2.
            child_indent = indent + 2
            sub = [(n, child_indent, rest)]
            while pos < len(lines) and lines[pos][1] >= child_indent:
                sub.append(lines[pos])
                pos += 1
            item, used = _map(sub, 0, child_indent)
            if used != len(sub):
                raise ValueError(f"line {sub[used][0]}: unexpected indentation")
            out.append(item)
        else:
            out.append(scalar(rest, n))
    return out, pos
