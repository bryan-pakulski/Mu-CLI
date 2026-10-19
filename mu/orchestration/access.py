"""Access policy on the master (plan §6.6): who may run what, where.

An ordered rule list, first match wins, evaluated by the pure ``decide``. A run targets many
nodes, so each node is judged on its own and the strictest result wins
(``deny`` > ``confirm: typed`` > ``confirm`` > ``allow``); the plan view shows which rule decided
for which nodes. A tool manifest's ``approval: confirm`` raises ``allow`` to ``confirm``.
Node-local policy (``node/policy.py``) still has the final veto.

Rule shape::

    {name, match: {actor: ai|operator, run: run|test, effect, category, tool: <glob>, nodes: <selector>},
     decision: allow | confirm | deny | {confirm: typed}}

``run`` separates canary tests of drafts (``test``) from ordinary runs (``run``). A rule without
``run`` matches only ordinary runs, so policies written before tests had rules keep their
meaning; tests that no rule matches fall back to ``confirm``. Whatever the rules say, tests only
target canary nodes, drafts are signed with the test key, a draft that changed after approval is
not dispatched and destructive effects keep typed confirmation (enforced in ``runs``/``hub``).
"""

from __future__ import annotations

import fnmatch
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from . import selectors
from .models import CATEGORIES, EFFECTS

ACTORS = ("ai", "operator")
RUN_KINDS = ("run", "test")
MATCH_KEYS = ("actor", "run", "effect", "category", "tool", "nodes")
LEVELS = ("allow", "confirm", "typed", "deny")
RANK = {name: i for i, name in enumerate(LEVELS)}
DEFAULT_FALLBACK = "confirm"

DEFAULT_RULES: List[Dict[str, Any]] = [
    {"name": "Tests are approved by the operator", "match": {"run": "test"}, "decision": "confirm"},
    {"name": "AI diagnostics outside prod", "match": {"actor": "ai", "effect": "read_only", "nodes": "env!=prod"},
     "decision": "allow"},
    {"name": "AI diagnostics on prod are confirmed first",
     "match": {"actor": "ai", "effect": "read_only", "nodes": "env=prod"}, "decision": "confirm"},
    {"name": "Operator read-only runs need no extra confirmation",
     "match": {"actor": "operator", "effect": "read_only"}, "decision": "allow"},
    {"name": "Anything that changes a system is confirmed", "match": {"effect": "mutating"},
     "decision": "confirm"},
    {"name": "Destructive changes are confirmed by typing the tool id", "match": {"effect": "destructive"},
     "decision": {"confirm": "typed"}},
]


def _level(decision: Any) -> str:
    if isinstance(decision, Mapping):
        return "typed" if decision.get("confirm") == "typed" else "confirm"
    value = str(decision or "")
    return value if value in ("allow", "confirm", "deny") else ""


def validate_rules(rules: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(rules, list):
        return ["rules must be a list"]
    for i, rule in enumerate(rules):
        where = f"rule {i + 1}"
        if not isinstance(rule, Mapping):
            errors.append(f"{where}: must be a mapping")
            continue
        if not str(rule.get("name") or "").strip():
            errors.append(f"{where}: name is required")
        match = rule.get("match") or {}
        if not isinstance(match, Mapping):
            errors.append(f"{where}: match must be a mapping")
            continue
        for key in match:
            if key not in MATCH_KEYS:
                errors.append(f"{where}: unknown match key {key!r}")
        if "actor" in match and match["actor"] not in ACTORS:
            errors.append(f"{where}: actor must be ai or operator")
        if "run" in match and match["run"] not in RUN_KINDS:
            errors.append(f"{where}: run must be run or test")
        if "effect" in match and match["effect"] not in EFFECTS:
            errors.append(f"{where}: effect must be one of {', '.join(EFFECTS)}")
        if "category" in match and match["category"] not in CATEGORIES:
            errors.append(f"{where}: unknown category {match['category']!r}")
        if "nodes" in match:
            try:
                selectors.parse(str(match["nodes"]))
            except selectors.SelectorError as exc:
                errors.append(f"{where}: {exc}")
        if not _level(rule.get("decision")):
            errors.append(f"{where}: decision must be allow, confirm, deny or {{confirm: typed}}")
    return errors


@dataclass
class NodeDecision:
    node_id: str
    level: str
    rule: str


@dataclass
class AccessDecision:
    level: str                      # allow | confirm | typed | deny
    rule: str                       # the rule that set the overall level
    per_node: List[NodeDecision] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.level == "allow"

    @property
    def denied(self) -> bool:
        return self.level == "deny"

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["summary"] = self.summary()
        return out

    def summary(self) -> str:
        words = {"allow": "allowed", "confirm": "needs confirmation", "typed": "needs typed confirmation",
                 "deny": "denied"}[self.level]
        return f"{words} by rule “{self.rule}”" if self.rule else f"{words} (no rule matched)"


def _rule_matches(rule: Mapping[str, Any], actor: str, manifest: Mapping[str, Any],
                  labels: Mapping[str, str], run: str = "run") -> bool:
    match = rule.get("match") or {}
    if match.get("run", "run") != run:
        return False
    if "actor" in match and match["actor"] != actor:
        return False
    if "effect" in match and match["effect"] != manifest.get("effect"):
        return False
    if "category" in match and match["category"] != manifest.get("category"):
        return False
    if "tool" in match and not fnmatch.fnmatchcase(str(manifest.get("id") or ""), str(match["tool"])):
        return False
    if "nodes" in match and not selectors.matches(str(match["nodes"]), labels):
        return False
    return True


def decide_one(rules: Sequence[Mapping[str, Any]], actor: str, manifest: Mapping[str, Any],
               labels: Mapping[str, str], run: str = "run") -> NodeDecision:
    for rule in rules:
        if _rule_matches(rule, actor, manifest, labels, run):
            return NodeDecision("", _level(rule.get("decision")), str(rule.get("name") or ""))
    return NodeDecision("", DEFAULT_FALLBACK, "")


def decide(rules: Sequence[Mapping[str, Any]], actor: str, manifest: Mapping[str, Any],
           nodes: Iterable[Mapping[str, Any]], *, run: str = "run") -> AccessDecision:
    """``nodes`` are ``{"id", "labels"}`` mappings. ``actor`` is ``ai`` or ``operator``; ``run`` is
    ``test`` for canary tests of drafts (tool or runbook), ``run`` otherwise."""
    actor = "ai" if str(actor).startswith("ai") else "operator"
    per_node: List[NodeDecision] = []
    for node in nodes:
        d = decide_one(rules, actor, manifest, node.get("labels") or {}, run)
        d.node_id = str(node.get("id") or "")
        per_node.append(d)
    if not per_node:
        d = decide_one(rules, actor, manifest, {}, run)
        per_node_view: List[NodeDecision] = []
        level, rule = d.level, d.rule
    else:
        worst = max(per_node, key=lambda d: RANK[d.level])
        level, rule = worst.level, worst.rule
        per_node_view = per_node
    reasons = []
    if manifest.get("approval") == "confirm" and RANK[level] < RANK["confirm"]:
        level = "confirm"
        rule = rule or ""
        reasons.append("the tool's manifest requires confirmation")
    if manifest.get("effect") == "destructive" and RANK[level] < RANK["typed"] and level != "deny":
        level = "typed"
        reasons.append("destructive tools are always confirmed by typing the tool id")
    return AccessDecision(level, rule, per_node_view, reasons)
