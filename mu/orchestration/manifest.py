"""Tool manifests (plan §3.2): normalization, validation, digests, YAML.

``normalize`` returns the canonical manifest (defaults filled, keys ordered) plus a list of
errors. The digest the master signs and the node re-computes is taken over the normalized form,
so equal manifests always hash equally. Input and output schemas are restricted to the subset
``node.schema`` implements, which keeps master and node validation identical.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from .models import CATEGORIES, EFFECTS
from .node import schema as jschema
from .node.envelope import manifest_digest
from .node.executor import BUILTINS

TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}(\.[a-z0-9][a-z0-9_-]{0,47}){1,4}$")
TOOL_REF = re.compile(r"^(?P<id>[a-z0-9][a-z0-9_.-]{0,240})@(?P<version>\d{1,6})$")
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SECRET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
USER_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
RUNTIMES = ("bash", "python3", "builtin")
APPROVALS = ("none", "confirm")
_RESERVED_ENV = ("MUCLI_", "LD_", "PYTHON", "BASH_ENV", "PATH", "HOME", "IFS", "SHELLOPTS")
KNOWN_KEYS = ("id", "version", "title", "summary", "description", "category", "effect", "idempotent",
              "targets", "privilege", "inputs", "outputs", "implementation", "limits", "rollback",
              "preconditions", "approval", "rollout", "tests", "secrets", "env", "paths")

DEFAULT_LIMITS = {"timeout_s": 300, "max_output_kb": 512, "cpu": 1.0, "memory_mb": 256}


def parse_ref(ref: str) -> Optional[Tuple[str, int]]:
    match = TOOL_REF.match(str(ref or "").strip())
    if not match:
        return None
    return match.group("id"), int(match.group("version"))


def _num(value: Any, lo: float, hi: float, name: str, errors: List[str], integer: bool = True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{name} must be a number")
        return None
    if integer and int(value) != value:
        errors.append(f"{name} must be an integer")
        return None
    if not (lo <= value <= hi):
        errors.append(f"{name} must be between {lo:g} and {hi:g}")
    return int(value) if integer else float(value)


def normalize(raw: Mapping[str, Any], *, version: Optional[int] = None) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    if not isinstance(raw, Mapping):
        return {}, ["manifest must be a mapping"]
    for key in raw:
        if key not in KNOWN_KEYS:
            errors.append(f"unknown manifest key {key!r}")
    m: Dict[str, Any] = {}
    tool_id = str(raw.get("id") or "").strip()
    if not TOOL_ID.match(tool_id):
        errors.append("id must be a dotted lowercase name like 'nginx.rotate-logs'")
    m["id"] = tool_id
    v = raw.get("version") if version is None else version
    m["version"] = int(v) if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 1
    m["title"] = str(raw.get("title") or "").strip()[:120]
    if not m["title"]:
        errors.append("title is required")
    m["summary"] = str(raw.get("summary") or "").strip()[:1000]
    if raw.get("description"):
        m["description"] = str(raw["description"])[:20000]
    m["category"] = str(raw.get("category") or "other")
    if m["category"] not in CATEGORIES:
        errors.append(f"category must be one of {', '.join(CATEGORIES)}")
    m["effect"] = str(raw.get("effect") or "")
    if m["effect"] not in EFFECTS:
        errors.append(f"effect must be one of {', '.join(EFFECTS)}")
    m["idempotent"] = bool(raw.get("idempotent", False))

    targets = raw.get("targets") or {}
    if not isinstance(targets, Mapping):
        errors.append("targets must be a mapping")
        targets = {}
    os_list = [str(x) for x in (targets.get("os") or ["linux"])]
    if any(o != "linux" for o in os_list):
        errors.append("targets.os: only linux is supported")
    m["targets"] = {"os": ["linux"], "requires": sorted({str(x) for x in (targets.get("requires") or [])})}

    privilege = raw.get("privilege") or {}
    run_as = str((privilege or {}).get("run_as") or "root") if isinstance(privilege, Mapping) else "root"
    if run_as != "root" and not USER_NAME.match(run_as):
        errors.append("privilege.run_as must be 'root' or a service user name")
    m["privilege"] = {"run_as": run_as}

    inputs = raw.get("inputs") or {"type": "object", "properties": {}, "additionalProperties": False}
    errors += [f"inputs: {p}" for p in jschema.unsupported_keywords(inputs, "inputs")]
    if isinstance(inputs, Mapping):
        if inputs.get("type") != "object":
            errors.append("inputs.type must be 'object'")
        for name in (inputs.get("properties") or {}):
            if not PARAM_NAME.match(str(name)):
                errors.append(f"inputs.{name}: parameter names must be lowercase snake_case")
        for name, sub in (inputs.get("properties") or {}).items():
            if isinstance(sub, Mapping) and "default" in sub:
                problems = jschema.validate(sub["default"], dict(sub), f"inputs.{name}.default")
                errors += problems
    m["inputs"] = dict(inputs) if isinstance(inputs, Mapping) else {}
    outputs = raw.get("outputs") or {}
    if outputs:
        errors += [f"outputs: {p}" for p in jschema.unsupported_keywords(outputs, "outputs")]
    m["outputs"] = dict(outputs) if isinstance(outputs, Mapping) else {}

    impl = raw.get("implementation") or {}
    if not isinstance(impl, Mapping):
        errors.append("implementation must be a mapping")
        impl = {}
    runtime = str(impl.get("runtime") or "")
    if runtime not in RUNTIMES:
        errors.append(f"implementation.runtime must be one of {', '.join(RUNTIMES)}")
    entry = str(impl.get("entry") or "")
    if runtime == "builtin":
        if entry not in BUILTINS:
            errors.append(f"implementation.entry must name a builtin ({', '.join(sorted(BUILTINS))})")
        if m["effect"] != "read_only":
            errors.append("builtin tools are read-only")
    elif not FILE_NAME.match(entry):
        errors.append("implementation.entry must be a plain file name")
    m["implementation"] = {"runtime": runtime, "entry": entry, "check_mode": bool(impl.get("check_mode", False))}

    limits = dict(DEFAULT_LIMITS)
    raw_limits = raw.get("limits") or {}
    if isinstance(raw_limits, Mapping):
        for key, lo, hi, integer in (("timeout_s", 1, 21600, True), ("max_output_kb", 1, 4096, True),
                                     ("cpu", 0.05, 64, False), ("memory_mb", 16, 65536, True)):
            if key in raw_limits:
                value = _num(raw_limits[key], lo, hi, f"limits.{key}", errors, integer)
                if value is not None:
                    limits[key] = value
    m["limits"] = limits

    rollback = str(raw.get("rollback") or "")
    if rollback and not parse_ref(rollback):
        errors.append("rollback must be a pinned tool reference like 'nginx.restore-logs@2'")
    m["rollback"] = rollback
    pre = [str(p) for p in (raw.get("preconditions") or [])]
    for ref in pre:
        if not parse_ref(ref):
            errors.append(f"precondition {ref!r} must be a pinned tool reference")
    m["preconditions"] = pre
    m["approval"] = str(raw.get("approval") or ("confirm" if m["effect"] != "read_only" else "none"))
    if m["approval"] not in APPROVALS:
        errors.append("approval must be 'none' or 'confirm'")
    if m["effect"] != "read_only" and m["approval"] == "none":
        errors.append("mutating and destructive tools require approval: confirm")

    rollout = raw.get("rollout") or {}
    m["rollout"] = {"max_nodes": 50, "default_batch": 10, "canary": 1}
    if isinstance(rollout, Mapping):
        for key, lo, hi in (("max_nodes", 1, 10000), ("default_batch", 1, 1000), ("canary", 0, 100)):
            if key in rollout:
                value = _num(rollout[key], lo, hi, f"rollout.{key}", errors)
                if value is not None:
                    m["rollout"][key] = value

    tests = raw.get("tests") or []
    m["tests"] = []
    if not isinstance(tests, list):
        errors.append("tests must be a list")
        tests = []
    for i, test in enumerate(tests):
        if not isinstance(test, Mapping):
            errors.append(f"tests[{i}] must be a mapping")
            continue
        params = test.get("params") or {}
        item = {"name": str(test.get("name") or f"test {i + 1}"), "params": dict(params),
                "check_mode": bool(test.get("check_mode", True)), "expect": dict(test.get("expect") or {})}
        if isinstance(m["inputs"], dict) and m["inputs"]:
            filled = jschema.apply_defaults(item["params"], m["inputs"])
            errors += [f"tests[{i}]: {e}" for e in jschema.validate(filled, m["inputs"], "params")]
        m["tests"].append(item)

    secret_names = [str(s) for s in (raw.get("secrets") or [])]
    for name in secret_names:
        if not SECRET_NAME.match(name):
            errors.append(f"secret name {name!r} must be lowercase snake_case")
    m["secrets"] = sorted(set(secret_names))
    env = raw.get("env") or {}
    m["env"] = {}
    if isinstance(env, Mapping):
        for name, value in env.items():
            if not ENV_NAME.match(str(name)) or str(name).startswith(_RESERVED_ENV):
                errors.append(f"env {name!r} is not allowed")
            else:
                m["env"][str(name)] = str(value)
    paths = raw.get("paths") or {}
    m["paths"] = {"write": [], "protect_system": False}
    if isinstance(paths, Mapping):
        writes = [str(p) for p in (paths.get("write") or [])]
        for p in writes:
            if not p.startswith("/") or ".." in p.split("/"):
                errors.append(f"paths.write {p!r} must be an absolute path")
        m["paths"] = {"write": writes, "protect_system": bool(paths.get("protect_system", False))}
    return m, errors


def digest_of(manifest: Mapping[str, Any]) -> str:
    return manifest_digest(manifest)


def to_yaml(manifest: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(manifest), sort_keys=False, allow_unicode=True, width=100)


def from_yaml(text: str) -> Dict[str, Any]:
    data = yaml.safe_load(text or "") or {}
    if not isinstance(data, dict):
        raise ValueError("manifest YAML must be a mapping")
    return data


def validate_params(manifest: Mapping[str, Any], params: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    inputs = manifest.get("inputs") or {}
    filled = jschema.apply_defaults(dict(params or {}), inputs)
    return filled, jschema.validate(filled, inputs, "params")
