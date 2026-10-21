"""Label selectors: ``env=prod,role in (web,api),!legacy`` (comma = AND).

Terms: ``k=v`` (or ``k==v``), ``k!=v``, ``k in (a,b)``, ``k notin (a,b)``, ``k`` (label exists),
``!k`` (label absent). An empty selector or ``*`` matches every node. Used by run targeting,
groups and the access policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Tuple

_KEY = r"[A-Za-z0-9][A-Za-z0-9_./-]{0,62}"
_VAL = r"[A-Za-z0-9_.:/-]{0,63}"
_SEL_VAL = r"[A-Za-z0-9_.:/-]{1,63}"   # a selector compares against a non-empty value
_TERM_PATTERNS = (
    ("in", re.compile(rf"^(?P<k>{_KEY})\s+in\s*\((?P<v>[^)]*)\)$")),
    ("notin", re.compile(rf"^(?P<k>{_KEY})\s+notin\s*\((?P<v>[^)]*)\)$")),
    ("ne", re.compile(rf"^(?P<k>{_KEY})\s*!=\s*(?P<v>{_SEL_VAL})$")),
    ("eq", re.compile(rf"^(?P<k>{_KEY})\s*==?\s*(?P<v>{_SEL_VAL})$")),
    ("absent", re.compile(rf"^!\s*(?P<k>{_KEY})$")),
    ("exists", re.compile(rf"^(?P<k>{_KEY})$")),
)
LABEL_KEY = re.compile(rf"^{_KEY}$")
LABEL_VALUE = re.compile(rf"^{_VAL}$")


class SelectorError(ValueError):
    pass


@dataclass(frozen=True)
class Term:
    op: str
    key: str
    values: Tuple[str, ...] = ()

    def matches(self, labels: Mapping[str, str]) -> bool:
        has = self.key in labels
        value = str(labels.get(self.key, ""))
        if self.op == "eq":
            return has and value == self.values[0]
        if self.op == "ne":
            return not has or value != self.values[0]
        if self.op == "in":
            return has and value in self.values
        if self.op == "notin":
            return not has or value not in self.values
        if self.op == "exists":
            return has
        if self.op == "absent":
            return not has
        return False


def _split(text: str) -> List[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if depth:
        raise SelectorError("unbalanced parentheses")
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def parse(text: str) -> Tuple[Term, ...]:
    text = str(text or "").strip()
    if text in ("", "*"):
        return ()
    terms = []
    for part in _split(text):
        for op, pattern in _TERM_PATTERNS:
            match = pattern.match(part)
            if match:
                values: Tuple[str, ...] = ()
                if op in ("in", "notin"):
                    values = tuple(v.strip() for v in match.group("v").split(",") if v.strip())
                    if not values:
                        raise SelectorError(f"empty value list in {part!r}")
                elif op in ("eq", "ne"):
                    values = (match.group("v"),)
                terms.append(Term(op, match.group("k"), values))
                break
        else:
            raise SelectorError(f"cannot parse selector term {part!r}")
    return tuple(terms)


def compile_selector(text: str) -> Callable[[Mapping[str, str]], bool]:
    terms = parse(text)
    return lambda labels: all(t.matches(labels or {}) for t in terms)


def matches(text: str, labels: Mapping[str, str]) -> bool:
    return compile_selector(text)(labels)


def validate_labels(labels: Mapping[str, str]) -> List[str]:
    errors = []
    for key, value in (labels or {}).items():
        if not LABEL_KEY.match(str(key)):
            errors.append(f"invalid label key {key!r}")
        if not LABEL_VALUE.match(str(value)):
            errors.append(f"invalid value for label {key!r}")
    return errors


def in_group(group: Mapping[str, Any], node_id: str, labels: Mapping[str, str]) -> bool:
    """Whether a node belongs to a group: an explicit member (dragged in), or its labels match the
    group's selector. An empty selector matches nothing (a members-only group); a bad one matches
    nothing either."""
    if str(node_id) in (group.get("members") or ()):
        return True
    text = str(group.get("selector") or "").strip()
    if not text:
        return False
    try:
        return matches(text, labels)
    except SelectorError:
        return False
