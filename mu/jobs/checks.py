"""Deterministic suites, JUnit evidence and baseline triage for the Checks step."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from .core.artifacts import artifacts_for
from .core.plan_ops import approved_plan_text
from .core.plan_doc import PlanDoc
from .dynamic_checks import allowed_executables, check_command
from .verification import VerificationCheck, VerificationRun, VerificationStore, changed_files as _changed_files


@dataclass(frozen=True)
class Suite:
    name: str
    command: str
    junit: str = ""
    env: str = ""   # build environment (binding name); blank = owner of cwd / named path / primary
    cwd: str = ""   # repository-relative working directory


def resolve_suites(job, settings=None):
    from .job_settings import load_settings
    settings = settings if settings is not None else load_settings()
    suites = {}
    plan = approved_plan_text(job)
    if plan:
        for check in PlanDoc.parse(plan).checks:
            suites[check.suite] = Suite(check.suite, check.cmd, env=check.env, cwd=check.cwd)
    else:
        suites = {f"check{i}": Suite(f"check{i}", cmd) for i, cmd in enumerate(job.validation_commands, 1)}
    configs = [settings.get("checks") or {}, (job.execution or {}).get("checks") or {}]
    repo = Path((job.metadata or {}).get("repository_root") or job.repository)
    config_path = repo / ".mucli/jobs.toml"
    if config_path.is_file():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        configs.append(tomllib.loads(config_path.read_text()).get("checks") or {})
    for config in configs:
        for raw in config.get("suites") or []:
            name = str(raw.get("name") or raw.get("suite") or "").strip()
            command = str(raw.get("command") or raw.get("cmd") or "").strip()
            if not name:
                raise ValueError("configured checks require a suite name")
            # Required configuration adds to the plan; a reused suite name must
            # not silently erase an approved check with a different command.
            if name in suites and suites[name].command != command:
                name = f"{name}-required-{len(suites) + 1}"
            suites[name] = Suite(name, command, str(raw.get("junit") or ""), env=str(raw.get("env") or "").strip(),
                                 cwd=str(raw.get("cwd") or "").strip())
    # Revalidate at execution, including configured commands and legacy jobs.
    for suite in suites.values():
        ok, reason = check_command(suite.command, allowed_executables(job))
        if not ok:
            raise ValueError(f"invalid check {suite.name}: {reason}")
        if suite.junit and (Path(suite.junit).is_absolute() or ".." in Path(suite.junit).parts):
            raise ValueError(f"JUnit path must be workspace-relative: {suite.junit}")
        if suite.cwd and (Path(suite.cwd).is_absolute() or ".." in Path(suite.cwd).parts):
            raise ValueError(f"check {suite.name}: cwd must be repository-relative: {suite.cwd}")
    return list(suites.values())


def parse_junit(text: str):
    if len(text) > 8 * 1024 * 1024 or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("unsafe or oversized JUnit report")
    root = ET.fromstring(text)
    cases = []
    suites = [root] if root.tag == "testsuite" else []
    suites += [s for s in root.iter("testsuite") if s is not root]
    owner = {}
    for suite_node in suites:
        for child in suite_node:
            if child.tag == "testcase":
                owner[id(child)] = suite_node.get("name", "")
    for node in root.iter("testcase"):
        failure = node.find("failure")
        if failure is None:
            failure = node.find("error")
        status = "failed" if failure is not None else (
            "skipped" if node.find("skipped") is not None else "passed")
        case = {"id": f"{node.get('classname', '')}::{node.get('name', '')}", "status": status,
                "time": node.get("time", "0"),
                "suite": node.get("classname") or owner.get(id(node), ""), "name": node.get("name", "")}
        if failure is not None:
            parts = [str(failure.get("message") or "").strip(), str(failure.text or "").strip()[:4000]]
            case["message"] = "\n".join(p for p in parts if p)
        cases.append(case)
    return {"total": len(cases), "passed": sum(c["status"] == "passed" for c in cases),
            "failed": sum(c["status"] == "failed" for c in cases),
            "skipped": sum(c["status"] == "skipped" for c in cases), "cases": cases}


def triage(current, baseline):
    """Only identical, observed failures can be attributed to the base commit."""
    current_ids = {c["id"] for c in current.get("junit", {}).get("cases", []) if c["status"] == "failed"}
    baseline_ids = {c["id"] for c in baseline.get("junit", {}).get("cases", []) if c["status"] == "failed"}
    if current_ids:
        return sorted(current_ids - baseline_ids), sorted(current_ids & baseline_ids)
    if current.get("passed"):
        return [], []
    if not current.get("timed_out") and not baseline.get("timed_out") and not baseline.get("passed"):
        log = str(current.get("stderr") or current.get("stdout") or "").strip()
        base_log = str(baseline.get("stderr") or baseline.get("stdout") or "").strip()
        if log and log == base_log and current.get("return_code") == baseline.get("return_code"):
            return [], ["suite failure (identical baseline output)"]
    return ["suite failed without matching baseline evidence"], []


# ------------------------------------------------------------------ honest gate (job_templates.md §5)
#
# A check is *unrunnable* when it never got to test the change: its command (or a tool its
# script runs) is missing, a dependency install could not reach the registry, or a tool the
# project declares (package.json devDependencies) was resolved outside the project, e.g. a
# global /usr/bin/eslint because node_modules is not installed. Unrunnable results are never
# triaged as pre-existing; the run is *unverified*.

_NOT_FOUND_RES = (
    re.compile(r"(?m)^(?:/bin/)?(?:ba|da|z)?sh: (?:line )?\d+: ([^\s:]+): (?:command )?not found"),
    re.compile(r"(?m)(?:^|: )([\w@./+-]+): command not found"),
    re.compile(r"(?m)^env: '?([\w@./+-]+)'?: No such file or directory"),
)
_MODULE_RE = re.compile(r"Cannot find (?:module|package) '([^']+)'")
_REGISTRY_RE = re.compile(r"(?:npm (?:ERR!|error) code (E403|ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ETIMEDOUT|ECONNRESET))"
                          r"|(?:\b(E403)\b[^\n]{0,80}(?:Forbidden|registry))"
                          r"|(?:getaddrinfo (ENOTFOUND|EAI_AGAIN) ([\w.-]+))")
_PKG_MANAGERS = ("npm", "pnpm", "yarn", "bun")
# package → executables it installs into node_modules/.bin, where they differ from the name.
_PACKAGE_BINS = {"typescript": ("tsc", "tsserver"), "@biomejs/biome": ("biome",), "@playwright/test": ("playwright",),
                 "@angular/cli": ("ng",), "@nestjs/cli": ("nest",), "@vue/cli-service": ("vue-cli-service",),
                 "@storybook/cli": ("sb", "storybook"), "@swc/cli": ("swc",), "@babel/cli": ("babel",),
                 "npm-run-all": ("npm-run-all", "run-p", "run-s"), "npm-run-all2": ("npm-run-all", "run-p", "run-s")}
_WRAPPERS = ("npx", "cross-env", "dotenv", "env", "time", "nice")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")


def _oxford(items):
    items = [f"`{i}`" for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _package_dir(workspace, suite):
    """(package directory, [(kind, name)] the command runs, package manager) of a suite."""
    try:
        tokens = shlex.split(str(getattr(suite, "command", "") or ""))
    except ValueError:
        return None, [], ""
    if not tokens:
        return None, [], ""
    root = Path(workspace) / str(getattr(suite, "cwd", "") or "")
    head = os.path.basename(tokens[0])
    if head == "npx":
        rest = [t for t in tokens[1:] if not t.startswith("-")]
        return root, [("tool", rest[0])] if rest else [], "npx"
    if head not in _PKG_MANAGERS:
        return root, [("tool", head)], ""
    rest, prefix, i = [], "", 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("--prefix", "-C", "--cwd", "--dir") and i + 1 < len(tokens):
            prefix, i = tokens[i + 1], i + 2
            continue
        flag = next((f for f in ("--prefix=", "--cwd=", "--dir=") if tok.startswith(f)), "")
        if flag:
            prefix = tok[len(flag):]
        else:
            rest.append(tok)
        i += 1
    words = [t for t in rest if not t.startswith("-")]
    package = root / prefix if prefix else root
    if not words:
        return package, [], head
    sub = words[0]
    if sub in ("test", "t", "tst"):
        return package, [("script", "test")], head
    if sub in ("run", "run-script") and len(words) > 1:
        return package, [("script", words[1])], head
    if sub in ("exec", "x", "dlx") and len(words) > 1:
        return package, [("tool", words[1])], head
    if head in ("yarn", "pnpm", "bun") and sub not in ("install", "add", "ci", "i"):
        return package, [("script", sub)], head
    return package, [], head


def _script_tools(scripts, name, depth=0):
    """Executables a package.json script runs (first word of each command; `npm run x` followed)."""
    text = str((scripts or {}).get(name) or "")
    out = []
    for part in re.split(r"&&|\|\||;|\|", text):
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        words = [w for w in words if not _ASSIGNMENT.fullmatch(w)]
        while words and os.path.basename(words[0]) in _WRAPPERS:
            words = [w for w in words[1:] if not w.startswith("-") and not _ASSIGNMENT.fullmatch(w)]
        if not words:
            continue
        head = os.path.basename(words[0])
        if head in _PKG_MANAGERS and depth < 3:
            sub = [w for w in words[1:] if not w.startswith("-")]
            if sub and sub[0] in ("run", "run-script") and len(sub) > 1:
                out += _script_tools(scripts, sub[1], depth + 1)
            elif sub and sub[0] in ("test", "t"):
                out += _script_tools(scripts, "test", depth + 1)
            continue
        if head not in out:
            out.append(head)
    return out


def _declared_bins(manifest):
    bins = {}
    for section in ("devDependencies", "dependencies", "optionalDependencies"):
        for package in dict(manifest.get(section) or {}):
            for name in _PACKAGE_BINS.get(package, (package.rsplit("/", 1)[-1],)):
                bins.setdefault(name, package)
    return bins


def _bin_installed(package_dir, workspace, tool):
    """``node_modules/.bin/<tool>`` in the package directory or an ancestor up to the worktree."""
    root = Path(workspace).resolve()
    current = Path(package_dir).resolve()
    while True:
        if (current / "node_modules" / ".bin" / tool).exists():
            return True
        if current == root or root not in current.parents:
            return False
        current = current.parent


def project_tool_gaps(workspace, suite):
    """Project-declared tools a JS suite runs that are not installed in node_modules/.bin:
    ``[(tool, package, package dir relative to the worktree)]``. Empty for non-JS suites and
    Yarn Plug'n'Play checkouts."""
    package_dir, targets, _manager = _package_dir(workspace, suite)
    if package_dir is None:
        return []
    manifest = _read_json(package_dir / "package.json")
    if not manifest:
        return []
    if any((Path(d) / ".pnp.cjs").exists() for d in (package_dir, workspace)):
        return []
    bins = _declared_bins(manifest)
    tools = []
    for kind, name in targets:
        tools += _script_tools(manifest.get("scripts") or {}, name) if kind == "script" else [name]
    try:
        rel = str(Path(package_dir).resolve().relative_to(Path(workspace).resolve()))
    except ValueError:
        rel = ""
    return [(tool, bins[tool], "" if rel == "." else rel) for tool in dict.fromkeys(tools)
            if tool in bins and not _bin_installed(package_dir, workspace, tool)]


def _install_command(manager, rel, workspace):
    manager = manager if manager in _PKG_MANAGERS else "npm"
    folder = Path(workspace) / rel
    if manager == "npm" and not (folder / "package-lock.json").exists():
        if (folder / "pnpm-lock.yaml").exists():
            manager = "pnpm"
        elif (folder / "yarn.lock").exists():
            manager = "yarn"
    verb = {"npm": "ci", "pnpm": "install --frozen-lockfile", "yarn": "install --frozen-lockfile",
            "bun": "install --frozen-lockfile"}[manager]
    if not rel:
        return f"{manager} {verb}"
    flag = {"npm": "--prefix", "pnpm": "-C", "yarn": "--cwd", "bun": "--cwd"}[manager]
    return f"{manager} {flag} {rel} {verb}"


def _node_modules_gap(tool, rel, manager, workspace, *, stray=False):
    where = f" in {rel}" if rel else ""
    cause = f"`{tool}` comes from node_modules{where}, which isn't installed"
    if stray:
        cause += " (a tool from outside the project ran instead)"
    return {"tool": tool, "kind": "node_modules", "cause": cause,
            "hint": f"Choose a template with job setup, or add `{_install_command(manager, rel, workspace)}` "
                    "to this template's job setup."}


def classify_unrunnable(result, suite, workspace):
    """``{tool, cause, hint, kind}`` when a check could not run, else ``None`` (§5).

    Passing and timed-out checks are never unrunnable. ``kind``: ``missing_tool``,
    ``node_modules`` (a project-declared tool is not installed), ``registry`` (an install could
    not reach the package registry) or ``not_executable``."""
    if result.get("passed") or result.get("timed_out"):
        return None
    text = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
    code = result.get("return_code")
    _pkg, _targets, manager = _package_dir(workspace, suite)
    gaps = project_tool_gaps(workspace, suite)
    gap_dirs = {tool: rel for tool, _package, rel in gaps}
    registry = _REGISTRY_RE.search(text)
    if registry:
        code_name = next((g for g in registry.groups()[:3] if g), "E403")
        host = registry.group(4) or ("registry.npmjs.org" if "npm" in text.lower() else "")
        return {"tool": host or "registry", "kind": "registry",
                "cause": f"a dependency install could not reach the package registry{f' ({host})' if host else ''}: "
                         f"{code_name}",
                "hint": f"Allow {host or 'the registry'} in the template's network allowlist (Environments → template → "
                        "Used for jobs) or in Settings → Extra egress domains, then re-run checks."}
    missing = str(result.get("missing_executable") or "")
    if not missing:
        match = next((m for m in (p.search(text) for p in _NOT_FOUND_RES) if m), None)
        missing = match.group(1) if match else ""
    if missing:
        name = os.path.basename(missing)
        if name in gap_dirs:
            return _node_modules_gap(name, gap_dirs[name], manager, workspace)
        return {"tool": name, "kind": "missing_tool", "cause": f"`{name}` is not installed in the build environment",
                "hint": f"Install `{name}` in the build environment, or choose a template that has it."}
    if code == 126:
        return {"tool": str(getattr(suite, "command", "") or "").split(" ")[0], "kind": "not_executable",
                "cause": "the command is not executable in the build environment (exit 126)",
                "hint": "Check the command's file mode and interpreter, or choose a template that has the tool."}
    if gaps:
        # The suite failed although a tool it runs is declared by the project but missing from
        # node_modules/.bin: whatever ran was not the project's tool (e.g. a global /usr/bin/eslint).
        tool, _package, rel = gaps[0]
        return _node_modules_gap(tool, rel, manager, workspace, stray=True)
    module = _MODULE_RE.search(text)
    if (module and not module.group(1).startswith((".", "/"))) or code == 127:
        name = module.group(1) if module else str(getattr(suite, "command", "") or "").split(" ")[0]
        return {"tool": name, "kind": "missing_tool",
                "cause": f"`{name}` is not installed in the build environment" + ("" if module else " (exit 127)"),
                "hint": "Install the project's dependencies (template job setup), or choose a template that has them."}
    if "ERR_MODULE_NOT_FOUND" in text:
        return {"tool": "module", "kind": "missing_tool", "cause": "a module could not be found (ERR_MODULE_NOT_FOUND)",
                "hint": "Install the project's dependencies (template job setup), then re-run checks."}
    return None


def unverified_message(unrunnable, *, template=""):
    """Why a run is unverified, naming the commands, the cause and the fix."""
    parts = []
    node = [u for u in unrunnable if u.get("kind") == "node_modules"]
    if node:
        tools = list(dict.fromkeys(u["tool"] for u in node))
        command = re.search(r"`([^`]+)`", str(node[0].get("hint") or ""))
        install = command.group(1) if command else "npm ci"
        target = f"template `{template}`'s job setup" if template else "this template's setup"
        parts.append(f"{_oxford(tools)} {'comes' if len(tools) == 1 else 'come'} from `node_modules`, which isn't "
                     f"installed. Choose a template with job setup, or add `{install}` to {target}.")
    for item in unrunnable:
        if item.get("kind") != "node_modules":
            parts.append(f"`{item.get('command') or item.get('suite')}`: {item.get('cause')}. {item.get('hint')}")
    return " ".join(dict.fromkeys(parts))


def unverified_causes(summary) -> str:
    """Short "tools/causes" text of an unverified run (``checks/summary.json``); '' when verified."""
    summary = summary if isinstance(summary, dict) else {}
    parts = [f"`{t}` missing" for t in summary.get("missing_tools") or []]
    for item in summary.get("unrunnable_suites") or []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "missing_tool" and item.get("tool") in (summary.get("missing_tools") or []):
            continue
        name = item.get("suite") or item.get("command") or "check"
        cause = str(item.get("cause") or item.get("tool") or "could not run")
        parts.append(f"`{name}`: {cause}")
    if not parts and summary.get("verified") is False:
        parts.append(f"{int(summary.get('unrunnable') or 1)} check(s) could not run")
    return "; ".join(dict.fromkeys(parts))[:1500]


def git(workspace, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", workspace, *args],
                          capture_output=True, text=True, timeout=60, check=True).stdout.strip()


# Lockfiles a package manager may rewrite during a suite (install/resolve).
LOCKFILES = frozenset({
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock",
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "Cargo.lock", "Gemfile.lock", "composer.lock",
    "go.sum", "flake.lock", "mix.lock", "pubspec.lock", "Podfile.lock", "packages.lock.json",
})


def status_entries(workspace):
    """``(XY, path)`` for every porcelain entry, untracked files listed individually."""
    raw = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", workspace, "status", "--porcelain=v1", "-z",
                          "--untracked-files=all"], capture_output=True, text=True, timeout=60, check=True).stdout
    parts, out, i = raw.split("\0"), [], 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if len(item) < 4:
            continue
        code, path = item[:2], item[3:]
        if code[0] in "RC":
            i += 1  # the rename/copy source follows as its own NUL-terminated field
        out.append((code, path))
    return out


def discard_byproducts(workspace, baseline, suite):
    """Undo what a suite left behind that is a build byproduct, not a source edit:
    new untracked files (removed) and rewritten lockfiles (restored to HEAD).
    Entries already present before checks started (``baseline``) are left alone,
    and any other change stays in place so the dirty-tree gate still fails."""
    found = []
    for code, path in status_entries(workspace):
        if (code, path) in baseline:
            continue
        if code == "??":
            git(workspace, "clean", "-f", "-q", "--", path)
            found.append({"suite": suite, "path": path, "kind": "untracked"})
        elif "M" in code and "D" not in code and "A" not in code and os.path.basename(path) in LOCKFILES:
            git(workspace, "checkout", "-q", "HEAD", "--", path)
            found.append({"suite": suite, "path": path, "kind": "lockfile"})
    return found


class ChecksStage:
    def __init__(self, service, *, run_suite=None, settings=None):
        self.service = service
        self._sandboxed = run_suite is None
        self._states: list = []
        self._owners: dict = {}
        if run_suite is None:
            from .sandbox.check_runner import run_suite_in_container
            run_suite = lambda job, workspace, suite: run_suite_in_container(
                job, workspace, suite, service=service, restart_cursor=self._restart_cursor,
                environment=self._owners.get(suite.name), states=self._states)
        self.run_suite = run_suite
        self.settings = settings

    def _run_batched(self, job, suites):
        """One container per build environment for all of its suites; environments run
        concurrently. Returns ``{suite name: result}``."""
        from concurrent.futures import ThreadPoolExecutor
        from .sandbox.check_runner import run_suites_in_container
        groups = {}
        for suite in suites:
            owner = self._owners.get(suite.name)
            groups.setdefault(owner["name"] if owner else "", (owner, []))[1].append(suite)

        def run(group):
            owner, members = group
            started = time.monotonic()
            results = run_suites_in_container(job, job.worktree, members, service=self.service,
                                              restart_cursor=self._restart_cursor, environment=owner, states=self._states)
            share = int((time.monotonic() - started) * 1000 / max(1, len(members)))
            for result in results:
                result.setdefault("duration_ms", share)
            return dict(zip((m.name for m in members), results))
        out = {}
        with ThreadPoolExecutor(max_workers=max(1, min(3, len(groups)))) as pool:
            for part in pool.map(run, list(groups.values())):
                out.update(part)
        return out

    def _assign_environments(self, job, suites):
        """Each suite runs in the build environment that owns it (§4)."""
        from .sandbox.environments import EnvironmentBlocked, binding_for_suite, ensure_environments, job_states
        from .sandbox.runner import planned_files
        self._states = ensure_environments(self.service, job) if self._sandboxed else job_states(job)
        files = planned_files(job)
        try:
            self._owners = {suite.name: binding_for_suite(self._states, suite, files) for suite in suites}
        except ValueError as exc:
            raise EnvironmentBlocked(str(exc)) from exc

    def verify(self, job):
        from .sandbox.runner import StageRestart
        while True:
            self._restart_cursor = self.service.store.event_cursor()
            try:
                return self._verify_once(job)
            except StageRestart:
                self.service.store.append_event(job.id, "stage_restarted", payload={"stage": "checks"})
                job = self.service.get(job.id)

    def _verify_once(self, job):
        started = time.time()
        suites = resolve_suites(job, self.settings)
        self._assign_environments(job, suites)
        art = artifacts_for(job).ensure()
        run_no = art.next_check_run()
        head = git(job.worktree, "rev-parse", "HEAD")
        initial_dirty = git(job.worktree, "status", "--porcelain")
        baseline_entries = set(status_entries(job.worktree))
        byproducts = []
        trigger = str((self.service.get(job.id).metadata or {}).get("checks_trigger") or "auto")
        if trigger not in ("auto", "recheck", "fix", "repair"):
            trigger = "auto"
        results = []
        batched = self._run_batched(job, suites) if self._sandboxed else {}
        if batched:
            byproducts += discard_byproducts(job.worktree, baseline_entries, ",".join(batched))
        for suite in suites:
            suite_started = time.monotonic()
            result = batched[suite.name] if suite.name in batched else self.run_suite(job, job.worktree, suite)
            elapsed = int((time.monotonic() - suite_started) * 1000)
            if suite.name not in batched:
                # Between suites: a suite's byproducts must not leak into the next one.
                found = discard_byproducts(job.worktree, baseline_entries, suite.name)
                byproducts += found
                if found:
                    result["byproducts"] = [b["path"] for b in found]
            owner = self._owners.get(suite.name)
            result.update(suite=suite.name, command=suite.command, environment=owner["name"] if owner else "",
                          environment_docker=bool(owner and owner.get("docker")))
            try:
                result["duration_ms"] = int(result.get("duration_ms") if result.get("duration_ms") is not None else elapsed)
            except (TypeError, ValueError):
                result["duration_ms"] = elapsed
            results.append(result)
        failing = [r for r in results if not r.get("passed")]
        missing_tools = sorted({r["missing_executable"] for r in results if r.get("missing_executable")})
        missing_environments = {r["missing_executable"]: r.get("environment") or "" for r in results if r.get("missing_executable")}
        for result in results:
            if result.get("missing_executable"):
                # The base commit would fail the same way; this is an environment gap, not a regression.
                result.update(new_failures=[], preexisting_failures=[], environment_error=result["stderr"])
        suite_of = {s.name: s for s in suites}
        unrunnable = []
        for result in results:
            gap = classify_unrunnable(result, suite_of[result["suite"]], job.worktree)
            if gap is None:
                result["status"] = "passed" if result.get("passed") else "failed"
                continue
            # Honest gate (job_templates.md §5): never triaged as pre-existing.
            result.update(status="unrunnable", unrunnable={k: gap[k] for k in ("tool", "cause", "hint")},
                          unrunnable_kind=gap["kind"], new_failures=[], preexisting_failures=[],
                          environment_error=result.get("environment_error") or gap["cause"])
            unrunnable.append({"suite": result["suite"], "command": result["command"], **gap})
        failing_for_triage = [r for r in failing if not r.get("missing_executable") and r.get("status") != "unrunnable"]
        from .job_settings import load_settings
        settings = self.settings if self.settings is not None else load_settings()
        baseline_enabled = (job.execution.get("checks") or {}).get("baseline_triage", (settings.get("checks") or {}).get("baseline_triage", True))
        if failing_for_triage and job.base_sha and baseline_enabled:
            with tempfile.TemporaryDirectory(prefix="mucli-check-base-") as tmp:
                baseline = str(Path(tmp) / "worktree")
                git(job.worktree, "worktree", "add", "--detach", baseline, job.base_sha)
                try:
                    for result in failing_for_triage:
                        suite = next(s for s in suites if s.name == result["suite"])
                        base_result = self.run_suite(job, baseline, suite)
                        new, preexisting = triage(result, base_result)
                        result.update(new_failures=new, preexisting_failures=preexisting, baseline=base_result)
                finally:
                    git(job.worktree, "worktree", "remove", "--force", baseline)
        for result in failing_for_triage:
            result.setdefault("new_failures", ["baseline unavailable"])
            result.setdefault("preexisting_failures", [])
        current_head = git(job.worktree, "rev-parse", "HEAD")
        dirty_status = _without_setup_files(job, git(job.worktree, "status", "--porcelain"))
        initial_dirty = _without_setup_files(job, initial_dirty)
        dirty = bool(initial_dirty or dirty_status or current_head != head)
        new_count = sum(len(r.get("new_failures", [])) for r in results) + int(dirty)
        old_count = sum(len(r.get("preexisting_failures", [])) for r in results)
        passed = bool(suites) and not failing and not dirty
        verified = bool(suites) and not unrunnable
        summary = {"run": run_no, "head_sha": head, "passed": passed, "verified": verified,
            "unrunnable": len(unrunnable), "unrunnable_suites": unrunnable, "new_failures": new_count,
            "preexisting_failures": old_count, "suites": results, "dirty_worktree": dirty,
            "checks_total": len(suites), "checks_passed": sum(bool(r.get("passed")) for r in results),
            "checks_failed": len(failing), "finished_at": time.time(),
            "started_at": started, "duration_ms": int((time.time() - started) * 1000), "trigger": trigger,
            "byproducts": byproducts,
            "missing_tools": missing_tools, "missing_tool_environments": missing_environments,
            "missing_docker_suites": [{"suite": r["suite"], "environment": r.get("environment") or ""} for r in results
                                      if r.get("missing_executable") == "docker" and not r.get("environment_docker")]}
        checks = [VerificationCheck(command=r["command"], return_code=r.get("return_code"),
            passed=bool(r.get("passed")), timed_out=bool(r.get("timed_out")), duration_ms=int(r.get("duration_ms", 0)),
            stdout=str(r.get("stdout") or "")[-24000:], stderr=str(r.get("stderr") or "")[-24000:],
            status=str(r.get("status") or ""), unrunnable=dict(r.get("unrunnable") or {})) for r in results]
        status = ("missing_contract" if not suites else "dirty_worktree" if dirty else "unverified" if unrunnable
                  else "passed" if passed else "failed")
        changed, additions, deletions = _changed_files(job.worktree, job.base_sha, head)
        run = VerificationRun(id=uuid.uuid4().hex, job_id=job.id, status=status, passed=passed,
            started_at=started, finished_at=time.time(), duration_ms=int((time.time() - started) * 1000),
            base_sha=job.base_sha, head_sha=head, branch=job.branch, worktree=job.worktree,
            checks=checks, summary=summary, changed_files=changed, additions=additions, deletions=deletions,
            dirty=dirty, dirty_status=dirty_status)
        VerificationStore(self.service.store).save(run)
        art.write_json(f"checks/run-{run_no}/summary.json", summary)
        art.write_json("checks/summary.json", summary)
        for index, result in enumerate(results, 1):
            art.write_text(f"checks/run-{run_no}/suite-{index}.log", str(result.get("stdout") or "") + "\n" + str(result.get("stderr") or ""))
            if result.get("junit_xml"):
                art.write_text(f"checks/run-{run_no}/suite-{index}.xml", result["junit_xml"])
        # The trigger describes one run; later automatic runs are "auto" again.
        fresh = self.service.get(job.id)
        if (fresh.metadata or {}).get("checks_trigger"):
            meta = dict(fresh.metadata)
            meta.pop("checks_trigger", None)
            self.service.store.update_runtime_fields(job.id, metadata_json=meta)
        self.service.store.append_event(job.id, "checks_completed", payload={k: v for k, v in summary.items() if k != "suites"})
        return run


def _without_setup_files(job, porcelain):
    """``git status --porcelain`` text minus entries produced by template job setup (§4) or by
    an environment repair (§7: untracked files the repair container left)."""
    from .sandbox.job_setup import setup_files
    from .sandbox.repair import repair_files
    produced = set(setup_files(job)) | set(repair_files(job))
    if not produced or not porcelain:
        return porcelain
    kept = []
    for line in str(porcelain).splitlines():
        path = line[3:].strip().strip('"')
        if path in produced or (path.endswith("/") and any(p.startswith(path) for p in produced)):
            continue
        kept.append(line)
    return "\n".join(kept)


def push_check_reason(service, job, *, run=None, allow_unverified=False):
    """A human review override never overrides the deterministic check gate.

    ``allow_unverified``: a run whose only problem is unrunnable checks (status ``unverified``)
    may be pushed with an override reason (job_templates.md §5); the caller enforces the reason."""
    run = run or VerificationStore(service.store).latest(job.id)
    unverified_ok = bool(allow_unverified and run is not None and run.status == "unverified"
                         and not (run.summary or {}).get("new_failures"))
    if run is None or not (run.passed or unverified_ok):
        return "push requires a successful check run"
    workspace = job.worktree or job.repository
    try:
        reference = "HEAD" if job.worktree else job.branch
        head = git(workspace, "rev-parse", "--verify", f"{reference}^{{commit}}")
        if job.worktree and _without_setup_files(job, git(workspace, "status", "--porcelain")):
            return "worktree changed since checks; run checks again"
    except (OSError, subprocess.SubprocessError):
        return "cannot verify the job branch; run checks again"
    if head != run.head_sha:
        return "branch changed since checks; run checks again"
    return ""
