"""Runbooks (plan §3.3): ordered steps over pinned catalog tool versions.

Step ``params`` may reference run inputs, earlier steps' outputs (on the same node) and node
labels/facts through ``${{ ... }}``; ``when:`` is a bare expression. The expression language
is deliberately tiny and has no calls, attribute access on Python objects or loops — only
literals, references (``inputs.x``, ``steps.check.outputs.free_pct``, ``node.labels.env``,
``steps.check.state``), comparisons, ``in``, ``and``/``or``/``not`` and parentheses.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .manifest import TOOL_ID, parse_ref
from .models import EFFECT_RANK, EFFECTS
from .node import schema as jschema

STEP_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
ON_FAILURE = ("stop", "continue", "rollback")
_TEMPLATE = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
_TOKEN = re.compile(r"\s*(?:(?P<num>-?\d+(?:\.\d+)?)|(?P<str>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")|"
                    r"(?P<op>==|!=|<=|>=|<|>|\(|\)|\[|\]|\.)|(?P<name>[A-Za-z_][A-Za-z0-9_-]*))")
MAX_EXPR = 500


class ExprError(ValueError):
    pass


def _tokens(text: str) -> List[Tuple[str, str]]:
    if len(text) > MAX_EXPR:
        raise ExprError("expression too long")
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise ExprError(f"unexpected character at {pos}: {text[pos:pos + 10]!r}")
        pos = match.end()
        kind = match.lastgroup
        out.append((kind, match.group(kind)))
    return out


class _Parser:
    def __init__(self, text: str, ctx: Mapping[str, Any]):
        self.toks = _tokens(text)
        self.i = 0
        self.ctx = ctx

    def peek(self, value: Optional[str] = None):
        if self.i >= len(self.toks):
            return None
        tok = self.toks[self.i]
        if value is not None and tok[1] != value:
            return None
        return tok

    def take(self, value: Optional[str] = None):
        tok = self.peek(value)
        if tok is None:
            raise ExprError(f"expected {value!r}" if value else "unexpected end of expression")
        self.i += 1
        return tok

    def parse(self):
        value = self.or_()
        if self.i != len(self.toks):
            raise ExprError(f"unexpected {self.toks[self.i][1]!r}")
        return value

    def or_(self):
        value = self.and_()
        while self.peek("or"):
            self.take()
            right = self.and_()
            value = bool(value) or bool(right)
        return value

    def and_(self):
        value = self.not_()
        while self.peek("and"):
            self.take()
            right = self.not_()
            value = bool(value) and bool(right)
        return value

    def not_(self):
        if self.peek("not"):
            self.take()
            return not bool(self.not_())
        return self.cmp()

    def cmp(self):
        left = self.primary()
        tok = self.peek()
        if tok and tok[1] in ("==", "!=", "<", "<=", ">", ">=", "in"):
            op = self.take()[1]
            right = self.primary()
            try:
                if op == "==":
                    return left == right
                if op == "!=":
                    return left != right
                if op == "in":
                    return left in right if right is not None else False
                if left is None or right is None:
                    return False
                return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[op]
            except TypeError:
                raise ExprError(f"cannot compare {type(left).__name__} with {type(right).__name__}")
        return left

    def primary(self):
        tok = self.take()
        kind, value = tok
        if value == "(":
            inner = self.or_()
            self.take(")")
            return inner
        if kind == "num":
            return float(value) if "." in value else int(value)
        if kind == "str":
            body = value[1:-1]
            return re.sub(r"\\(.)", r"\1", body)
        if kind == "name":
            if value.startswith("__"):
                raise ExprError(f"name {value!r} is not allowed")
            if value == "true":
                return True
            if value == "false":
                return False
            if value == "null":
                return None
            if value not in ("inputs", "steps", "node"):
                raise ExprError(f"unknown name {value!r} (use inputs, steps or node)")
            current: Any = self.ctx.get(value)
            while True:
                if self.peek("."):
                    self.take()
                    name = self.take()
                    if name[0] != "name" or name[1].startswith("__"):
                        raise ExprError("expected a field name after '.'")
                    current = current.get(name[1]) if isinstance(current, Mapping) else None
                elif self.peek("["):
                    self.take()
                    idx = self.take()
                    self.take("]")
                    key: Any = idx[1]
                    if idx[0] == "num":
                        key = int(key)
                    elif idx[0] == "str":
                        key = key[1:-1]
                    if isinstance(current, list) and isinstance(key, int):
                        current = current[key] if -len(current) <= key < len(current) else None
                    elif isinstance(current, Mapping):
                        current = current.get(key)
                    else:
                        current = None
                else:
                    return current
        raise ExprError(f"unexpected {value!r}")


def evaluate(expr: str, ctx: Mapping[str, Any]) -> Any:
    return _Parser(str(expr), ctx).parse()


def render(value: Any, ctx: Mapping[str, Any]) -> Any:
    """Resolve ``${{ }}`` templates in a params structure. A whole-string template keeps its type."""
    if isinstance(value, str):
        whole = _TEMPLATE.fullmatch(value.strip())
        if whole:
            return evaluate(whole.group(1), ctx)
        return _TEMPLATE.sub(lambda m: "" if (v := evaluate(m.group(1), ctx)) is None else str(v), value)
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    return value


def check_syntax(expr: str) -> Optional[str]:
    try:
        evaluate(expr, {"inputs": {}, "steps": {}, "node": {}})
        return None
    except ExprError as exc:
        return str(exc)




# --------------------------------------------------------------------- analysis
#
# ``analyze`` is the one validator behind saving, the editor's live checks, review and
# publishing. Every problem is ``{path, message, severity}`` where ``path`` names the field
# (``steps[2].params.keep_days``) so the editor can show it in place; ``normalize`` keeps the
# older ``"path: message"`` strings (errors only) for existing callers.

ROOTS = ("inputs", "steps", "node")
STEP_FIELDS = ("outputs", "state", "exit_code")
NODE_FIELDS = ("id", "name", "labels", "facts")


def problem(path: str, message: str, severity: str = "error") -> Dict[str, str]:
    return {"path": path, "message": message, "severity": severity}


def problem_text(p: Mapping[str, Any]) -> str:
    return f"{p['path']}: {p['message']}" if p.get("path") else str(p["message"])


def errors_of(problems) -> List[Dict[str, Any]]:
    return [p for p in problems if p.get("severity", "error") == "error"]


def references(expr: str) -> List[List[Any]]:
    """Every ``inputs.*``, ``steps.*`` and ``node.*`` reference in an expression, as path segments
    (``steps.check.outputs.disks[0]`` → ``["steps", "check", "outputs", "disks", 0]``)."""
    toks = _tokens(str(expr))
    out: List[List[Any]] = []
    i = 0
    while i < len(toks):
        kind, value = toks[i]
        if kind == "name" and value in ROOTS and not (i and toks[i - 1][1] == "."):
            path: List[Any] = [value]
            i += 1
            while i < len(toks):
                if toks[i][1] == "." and i + 1 < len(toks) and toks[i + 1][0] == "name":
                    path.append(toks[i + 1][1])
                    i += 2
                elif toks[i][1] == "[" and i + 2 < len(toks) and toks[i + 2][1] == "]":
                    k, v = toks[i + 1]
                    path.append(int(v) if k == "num" else (v[1:-1] if k == "str" else v))
                    i += 3
                else:
                    break
            out.append(path)
            continue
        i += 1
    return out


def has_template(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_TEMPLATE.search(value))
    if isinstance(value, list):
        return any(has_template(v) for v in value)
    if isinstance(value, Mapping):
        return any(has_template(v) for v in value.values())
    return False


def _templates(value: Any, where: str):
    """``(path, expression)`` for every ``${{ }}`` in a params structure."""
    if isinstance(value, str):
        for match in _TEMPLATE.finditer(value):
            yield where, match.group(1)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _templates(v, f"{where}[{i}]")
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _templates(v, f"{where}.{k}")


def _check_expr(expr: str, where: str, scope: Mapping[str, Any], problems: List[Dict[str, str]]) -> None:
    """Syntax, then meaning: references must name declared inputs and *earlier* steps."""
    syntax = check_syntax(expr)
    if syntax:
        problems.append(problem(where, syntax))
        return
    declared = scope["inputs"]
    earlier: Mapping[str, Any] = scope["earlier"]
    for ref in references(expr):
        root = ref[0]
        if root == "inputs" and len(ref) > 1 and declared is not None and ref[1] not in declared:
            problems.append(problem(where, f"inputs.{ref[1]} is not a declared input"))
        elif root == "steps" and len(ref) > 1:
            name = ref[1]
            if name not in earlier:
                if name in scope["later"]:
                    problems.append(problem(where, f"steps.{name} runs after this step, so it is always null here"))
                else:
                    problems.append(problem(where, f"steps.{name} is not an earlier step"))
                continue
            if len(ref) > 2 and ref[2] not in STEP_FIELDS:
                problems.append(problem(where, f"steps.{name}.{ref[2]}: use outputs, state or exit_code"))
            elif len(ref) > 3 and ref[2] == "outputs" and earlier[name] is not None:
                ref_tool, outputs = earlier[name]
                props = outputs.get("properties") if isinstance(outputs, Mapping) else None
                if (isinstance(props, Mapping) and props and ref[3] not in props) or not outputs:
                    problems.append(problem(where, f"{ref_tool} does not declare an output {ref[3]!r}", "warning"))
        elif root == "node" and len(ref) > 1 and ref[1] not in NODE_FIELDS:
            problems.append(problem(where, f"node.{ref[1]}: use node.labels, node.facts, node.name or node.id"))


def _check_params(params: Mapping[str, Any], schema: Mapping[str, Any], where: str, ref: str,
                  problems: List[Dict[str, str]]) -> None:
    """Literal params against the tool's input schema; templates are typed only at run time."""
    props = schema.get("properties") or {}
    extra = schema.get("additionalProperties", True)
    for name, value in params.items():
        path = f"{where}.{name}"
        if name not in props:
            if extra is False:
                problems.append(problem(path, f"{ref} has no parameter {name!r}"))
            continue
        if has_template(value):
            continue
        for err in jschema.validate(value, props[name], str(name)):
            problems.append(problem(path, err))
    for name in schema.get("required") or []:
        if name not in params and "default" not in (props.get(name) or {}):
            problems.append(problem(f"{where}.{name}", f"{name} is required by {ref}"))


def tool_info(tv, latest: Optional[int] = None) -> Dict[str, Any]:
    m = tv.manifest
    return {"ref": tv.ref, "tool_id": tv.tool_id, "version": tv.version, "title": m.get("title") or tv.tool_id,
            "summary": m.get("summary") or "", "category": m.get("category") or "", "effect": tv.effect,
            "status": tv.status, "inputs": m.get("inputs") or {}, "outputs": m.get("outputs") or {},
            "rollback": m.get("rollback") or "", "preconditions": list(m.get("preconditions") or []),
            "check_mode": bool((m.get("implementation") or {}).get("check_mode")),
            "approval": m.get("approval") or "none", "latest": latest,
            "latest_ref": f"{tv.tool_id}@{latest}" if latest else ""}


def analyze(raw: Mapping[str, Any], *, version: Optional[int] = None, resolve_tool=None,
            allow_in_review: bool = False, latest_published=None
            ) -> Tuple[Dict[str, Any], List[Dict[str, str]], List[Dict[str, Any]]]:
    """``(spec, problems, steps_info)``. ``resolve_tool(tool_id, version)`` returns a ToolVersion
    or None; ``latest_published(tool_id)`` the newest published version number or None.

    ``allow_in_review`` accepts steps whose tool is itself still in review (a runbook may be
    reviewed alongside its tools); publishing always requires published tools."""
    problems: List[Dict[str, str]] = []
    info: List[Dict[str, Any]] = []
    if not isinstance(raw, Mapping):
        return {}, [problem("", "runbook must be a mapping")], info
    spec: Dict[str, Any] = {}
    rid = str(raw.get("id") or "")
    if not TOOL_ID.match(rid):
        problems.append(problem("id", "must be a dotted lowercase name like 'web.safe-deploy'"))
    spec["id"] = rid
    v = raw.get("version") if version is None else version
    spec["version"] = int(v) if isinstance(v, int) and v > 0 else 1
    spec["title"] = str(raw.get("title") or "").strip()[:120]
    if not spec["title"]:
        problems.append(problem("title", "a title is required"))
    spec["summary"] = str(raw.get("summary") or "")[:1000]
    inputs = raw.get("inputs") or {"type": "object", "properties": {}, "additionalProperties": False}
    for p in jschema.unsupported_keywords(inputs, "inputs"):
        problems.append(problem("inputs", p))
    spec["inputs"] = dict(inputs) if isinstance(inputs, Mapping) else {}
    props = spec["inputs"].get("properties")
    declared = set(props) if isinstance(props, Mapping) else None
    steps_raw = raw.get("steps") or []
    if not isinstance(steps_raw, list) or not steps_raw:
        problems.append(problem("steps", "a runbook needs at least one step"))
        steps_raw = []
    if len(steps_raw) > 50:
        problems.append(problem("steps", "a runbook may have at most 50 steps"))
    all_names = [str(s.get("name") or f"step{i + 1}") if isinstance(s, Mapping) else f"step{i + 1}"
                 for i, s in enumerate(steps_raw)]
    steps: List[Dict[str, Any]] = []
    earlier: Dict[str, Any] = {}
    effect = "read_only"
    for i, step in enumerate(steps_raw):
        where = f"steps[{i}]"
        if not isinstance(step, Mapping):
            problems.append(problem(where, "a step must be a mapping"))
            info.append({"index": i, "name": all_names[i], "tool": "", "ok": False})
            continue
        name = all_names[i]
        if not STEP_NAME.match(name):
            problems.append(problem(f"{where}.name", "names use lowercase letters, digits, '-' and '_'"))
        elif name in all_names[:i]:
            problems.append(problem(f"{where}.name", f"another step is already called {name!r}"))
        tool_ref = str(step.get("tool") or "")
        ref = parse_ref(tool_ref)
        if not ref:
            problems.append(problem(f"{where}.tool", "choose a pinned tool version like 'sys.disk-usage@1'"))
        on_failure = str(step.get("on_failure") or "stop")
        if on_failure not in ON_FAILURE:
            problems.append(problem(f"{where}.on_failure", "must be stop, continue or rollback"))
        raw_params = step.get("params") or {}
        if not isinstance(raw_params, Mapping):
            problems.append(problem(f"{where}.params", "params must be a mapping"))
            raw_params = {}
        params = dict(raw_params)
        when = str(step.get("when") or "")
        nodes = str(step.get("nodes") or "")
        if nodes:
            from . import selectors
            try:
                selectors.parse(nodes)
            except selectors.SelectorError as exc:
                problems.append(problem(f"{where}.nodes", str(exc)))
        item = {"name": name, "tool": tool_ref, "params": params, "when": when, "on_failure": on_failure,
                "nodes": nodes}
        scope = {"inputs": declared, "earlier": earlier, "later": set(all_names[i + 1:]) - set(all_names[:i + 1])}
        for path, expr in _templates(params, f"{where}.params"):
            _check_expr(expr, path, scope, problems)
        if when:
            _check_expr(when, f"{where}.when", scope, problems)
        step_info: Dict[str, Any] = {"index": i, "name": name, "tool": tool_ref, "ok": False}
        outputs_entry = None
        if ref and resolve_tool is not None:
            tv = resolve_tool(*ref)
            if tv is None:
                problems.append(problem(f"{where}.tool", f"tool {tool_ref} does not exist"))
            else:
                latest = latest_published(tv.tool_id) if latest_published else None
                step_info.update(tool_info(tv, latest), ok=True, index=i, name=name, tool=tool_ref)
                outputs_entry = (tv.ref, tv.manifest.get("outputs") or {})
                step_effect = tv.manifest.get("effect", "destructive")
                if EFFECT_RANK.get(step_effect, 2) > EFFECT_RANK[effect]:
                    effect = step_effect
                if tv.status not in ("published", "deprecated") and not (allow_in_review and tv.status == "in_review"):
                    problems.append(problem(f"{where}.tool", f"tool {tool_ref} is {tv.status}; runbooks use published tools"))
                elif tv.status == "deprecated":
                    hint = f"; upgrade to {tv.tool_id}@{latest}" if latest and latest > tv.version else ""
                    problems.append(problem(f"{where}.tool", f"{tool_ref} is deprecated and runs refuse it{hint}",
                                            "warning"))
                _check_params(params, tv.manifest.get("inputs") or {}, f"{where}.params", tv.ref, problems)
                if step_effect != "read_only" and not tv.manifest.get("rollback"):
                    problems.append(problem(where, f"{tool_ref} changes systems and has no rollback tool", "warning"))
                if on_failure == "rollback" and not tv.manifest.get("rollback"):
                    problems.append(problem(f"{where}.on_failure",
                                            f"{tool_ref} has no rollback tool, so a failure here only stops", "warning"))
        earlier[name] = outputs_entry
        info.append(step_info)
        steps.append(item)
    spec["steps"] = steps
    spec["effect"] = effect if effect in EFFECTS else "destructive"
    return spec, problems, info


def normalize(raw: Mapping[str, Any], *, version: Optional[int] = None,
              resolve_tool=None, allow_in_review: bool = False) -> Tuple[Dict[str, Any], List[str]]:
    """The normalized spec and its errors as ``"path: message"`` strings (warnings omitted)."""
    spec, problems, _info = analyze(raw, version=version, resolve_tool=resolve_tool, allow_in_review=allow_in_review)
    return spec, [problem_text(p) for p in errors_of(problems)]


# --------------------------------------------------------------------- YAML positions and preview

def parse_yaml(text: str) -> Tuple[Any, Optional[Dict[str, Any]]]:
    """``(data, None)`` or ``(None, {message, line, column})`` (1-based) for the editor's gutter."""
    import yaml
    try:
        return yaml.safe_load(text or ""), None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        message = " ".join(str(getattr(exc, "problem", "") or exc).split())
        return None, {"message": f"YAML doesn't parse: {message}", "line": mark.line + 1 if mark else None,
                      "column": mark.column + 1 if mark else None}


def yaml_positions(text: str) -> Dict[str, Tuple[int, int]]:
    """Field path (``steps[1].params.days``) → 1-based ``(line, column)`` of its key in ``text``."""
    import yaml
    try:
        root = yaml.compose(text or "")
    except yaml.YAMLError:
        return {}
    out: Dict[str, Tuple[int, int]] = {}

    def walk(node, path: str) -> None:
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                sub = f"{path}.{key.value}" if path else str(key.value)
                out[sub] = (key.start_mark.line + 1, key.start_mark.column + 1)
                walk(value, sub)
        elif isinstance(node, yaml.SequenceNode):
            for i, value in enumerate(node.value):
                sub = f"{path}[{i}]"
                out[sub] = (value.start_mark.line + 1, value.start_mark.column + 1)
                walk(value, sub)

    if root is not None:
        walk(root, "")
    return out


def locate(path: str, positions: Mapping[str, Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    """The position of ``path`` or of its nearest parent that appears in the YAML."""
    while path:
        if path in positions:
            return positions[path]
        cut = max(path.rfind("."), path.rfind("["))
        path = path[:cut] if cut > 0 else ""
    return None


def preview(spec: Mapping[str, Any], *, node: Mapping[str, Any], inputs: Mapping[str, Any]) -> Dict[str, Any]:
    """What each ``when:`` and param resolves to on one node with sample inputs, before anything
    runs: earlier steps' outputs are unknown, so they are null here."""
    filled = jschema.apply_defaults(dict(inputs or {}), spec.get("inputs") or {})
    ctx = {"inputs": filled, "steps": {}, "node": {"id": node.get("id", ""), "name": node.get("name", ""),
                                                   "labels": dict(node.get("labels") or {}),
                                                   "facts": dict(node.get("facts") or {})}}
    out = []
    from . import selectors
    for step in spec.get("steps") or []:
        row: Dict[str, Any] = {"name": step.get("name"), "runs_here": True}
        if step.get("nodes"):
            try:
                row["runs_here"] = selectors.matches(step["nodes"], ctx["node"]["labels"])
            except selectors.SelectorError as exc:
                row["nodes_error"] = str(exc)
        if step.get("when"):
            try:
                row["when"] = {"value": evaluate(step["when"], ctx)}
            except ExprError as exc:
                row["when"] = {"error": str(exc)}
        try:
            row["params"] = render(step.get("params") or {}, ctx)
        except ExprError as exc:
            row["params_error"] = str(exc)
        out.append(row)
    return {"inputs": filled, "node": {"id": ctx["node"]["id"], "name": ctx["node"]["name"]}, "steps": out}
