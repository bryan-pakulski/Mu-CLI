"""Toolchain detection from repository manifests (pure; no Docker, no network).

:func:`detect_toolchains` looks at the tracked file list (and, when a reader is given, a few
manifest contents) and returns what the repository is built with: a suggested base image for a
build environment, default check commands, the package-registry hosts its installs need and the
validation allowlist entries those commands use. The planner and the New job sheet show it;
nothing here changes a job by itself.
"""
from __future__ import annotations

import posixpath
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

Reader = Callable[[str], Optional[str]]

# Directories never scanned for manifests (vendored or generated trees).
_SKIP_PARTS = frozenset({"node_modules", "vendor", "third_party", ".git", "target", "build", "dist", ".venv", "venv"})
MAX_DEPTH = 3


def _toolchain(name: str, manifest: str, image: str, checks: List[str], *, lockfile: str = "",
               egress: Iterable[str] = (), allow: Iterable[str] = ()) -> Dict[str, Any]:
    root = posixpath.dirname(manifest)
    return {"name": name, "manifest": manifest, "root": root, "lockfile": lockfile, "base_image": image,
            "checks": checks, "egress": list(egress), "validation_allowlist": list(allow)}


PYPI = ("pypi.org", "files.pythonhosted.org")
NPM = ("registry.npmjs.org",)
CRATES = ("crates.io", "index.crates.io", "static.crates.io")
GO = ("proxy.golang.org", "sum.golang.org")
MAVEN = ("repo.maven.apache.org", "repo1.maven.org")
GRADLE = ("services.gradle.org", "plugins.gradle.org", "repo.maven.apache.org")
RUBYGEMS = ("rubygems.org", "index.rubygems.org")
HEX = ("hex.pm", "repo.hex.pm", "builds.hex.pm")
NUGET = ("api.nuget.org",)


def _python(manifest: str, siblings: set, read: Optional[Reader]) -> Dict[str, Any]:
    root = posixpath.dirname(manifest)
    join = (lambda name: posixpath.join(root, name) if root else name)
    text = (read(manifest) if read else None) or ""
    if join("uv.lock") in siblings:
        return _toolchain("python-uv", manifest, "ghcr.io/astral-sh/uv:python3.12-bookworm",
                          ["uv run pytest"], lockfile=join("uv.lock"), egress=PYPI, allow=["uv"])
    if join("poetry.lock") in siblings or "[tool.poetry]" in text:
        return _toolchain("python-poetry", manifest, "python:3.12-bookworm", ["poetry run pytest"],
                          lockfile=join("poetry.lock") if join("poetry.lock") in siblings else "",
                          egress=PYPI, allow=["poetry"])
    if join("pdm.lock") in siblings or "[tool.pdm]" in text:
        return _toolchain("python-pdm", manifest, "python:3.12-bookworm", ["pdm run pytest"],
                          lockfile=join("pdm.lock") if join("pdm.lock") in siblings else "", egress=PYPI, allow=["pdm"])
    if "[tool.hatch" in text:
        return _toolchain("python-hatch", manifest, "python:3.12-bookworm", ["hatch test"], egress=PYPI, allow=["hatch"])
    return _toolchain("python", manifest, "python:3.12-bookworm", ["python -m pytest"], egress=PYPI)


def _node(manifest: str, siblings: set, read: Optional[Reader]) -> Dict[str, Any]:
    root = posixpath.dirname(manifest)
    join = (lambda name: posixpath.join(root, name) if root else name)
    text = (read(manifest) if read else None) or ""
    has_test = bool(re.search(r'"test"\s*:', text)) if text else True
    if join("bun.lockb") in siblings or join("bun.lock") in siblings:
        lock = join("bun.lockb") if join("bun.lockb") in siblings else join("bun.lock")
        return _toolchain("bun", manifest, "oven/bun:1", ["bun test"], lockfile=lock, egress=NPM, allow=["bun"])
    if join("pnpm-lock.yaml") in siblings:
        return _toolchain("node-pnpm", manifest, "node:22-bookworm", ["pnpm test"] if has_test else [],
                          lockfile=join("pnpm-lock.yaml"), egress=NPM, allow=["pnpm"])
    if join("yarn.lock") in siblings:
        return _toolchain("node-yarn", manifest, "node:22-bookworm", ["yarn test"] if has_test else [],
                          lockfile=join("yarn.lock"), egress=(*NPM, "registry.yarnpkg.com"), allow=["yarn"])
    if join("deno.json") in siblings or join("deno.lock") in siblings:
        return _toolchain("deno", manifest, "denoland/deno:latest", ["deno test"], egress=("deno.land", "jsr.io", *NPM),
                          allow=["deno"])
    lock = join("package-lock.json") if join("package-lock.json") in siblings else ""
    return _toolchain("node-npm", manifest, "node:22-bookworm", ["npm test"] if has_test else [], lockfile=lock,
                      egress=NPM, allow=["npm"])


def _gradle(manifest: str, siblings: set, read: Optional[Reader]) -> Dict[str, Any]:
    root = posixpath.dirname(manifest)
    wrapper = (posixpath.join(root, "gradlew") if root else "gradlew") in siblings
    command = "./gradlew test" if wrapper and not root else "gradle test"
    return _toolchain("gradle", manifest, "gradle:8-jdk21", [command], egress=GRADLE,
                      allow=["./gradlew" if wrapper else "gradle"])


def _maven(manifest: str, siblings: set, read: Optional[Reader]) -> Dict[str, Any]:
    root = posixpath.dirname(manifest)
    wrapper = (posixpath.join(root, "mvnw") if root else "mvnw") in siblings
    command = "./mvnw -B test" if wrapper and not root else "mvn -B test"
    return _toolchain("maven", manifest, "maven:3-eclipse-temurin-21", [command], egress=MAVEN,
                      allow=["./mvnw" if wrapper else "mvn"])


_SIMPLE = {
    "go.mod": lambda m, s, r: _toolchain("go", m, "golang:1.23-bookworm", ["go test ./..."], lockfile=_sib(m, "go.sum", s),
                                         egress=GO, allow=["go"]),
    "Cargo.toml": lambda m, s, r: _toolchain("rust", m, "rust:1-bookworm", ["cargo test"], lockfile=_sib(m, "Cargo.lock", s),
                                             egress=CRATES, allow=["cargo"]),
    "pom.xml": _maven,
    "build.gradle": _gradle,
    "build.gradle.kts": _gradle,
    "pyproject.toml": _python,
    "package.json": _node,
    "Gemfile": lambda m, s, r: _toolchain("ruby", m, "ruby:3.3-bookworm",
                                          ["bundle exec rake test"] if _sib(m, "Rakefile", s) else ["bundle exec rspec"],
                                          lockfile=_sib(m, "Gemfile.lock", s), egress=RUBYGEMS, allow=["bundle", "rake"]),
    "mix.exs": lambda m, s, r: _toolchain("elixir", m, "elixir:1.17", ["mix test"], lockfile=_sib(m, "mix.lock", s),
                                          egress=HEX, allow=["mix"]),
    "Package.swift": lambda m, s, r: _toolchain("swift", m, "swift:6.0", ["swift test"], lockfile=_sib(m, "Package.resolved", s),
                                                egress=("github.com",), allow=["swift"]),
    "deno.json": lambda m, s, r: _toolchain("deno", m, "denoland/deno:latest", ["deno test"],
                                            lockfile=_sib(m, "deno.lock", s), egress=("deno.land", "jsr.io"), allow=["deno"]),
}


def _sib(manifest: str, name: str, siblings: set) -> str:
    root = posixpath.dirname(manifest)
    path = posixpath.join(root, name) if root else name
    return path if path in siblings else ""


def _scannable(path: str) -> bool:
    parts = path.split("/")
    return len(parts) - 1 <= MAX_DEPTH and not any(p in _SKIP_PARTS for p in parts[:-1])


def detect_toolchains(paths: Iterable[str], read: Optional[Reader] = None) -> List[Dict[str, Any]]:
    """Toolchains of a repository from its tracked ``paths`` (repository-relative).

    ``read(path)`` optionally returns a manifest's text (pyproject tool tables, package.json
    scripts). Manifests deeper than :data:`MAX_DEPTH` or inside vendored trees are ignored; one
    entry per (component root, toolchain), root components first."""
    files = {str(p).strip("/") for p in paths if p}
    found: List[Dict[str, Any]] = []
    seen = set()
    for path in sorted(files, key=lambda p: (p.count("/"), p)):
        if not _scannable(path):
            continue
        base = posixpath.basename(path)
        root = posixpath.dirname(path)
        if base in _SIMPLE:
            if base == "build.gradle.kts" and (posixpath.join(root, "build.gradle") if root else "build.gradle") in files:
                continue
            if base == "deno.json" and (posixpath.join(root, "package.json") if root else "package.json") in files:
                continue  # package.json's detector already knows deno
            entry = _SIMPLE[base](path, files, read)
        elif base.endswith((".csproj", ".fsproj")) or base.endswith(".sln"):
            if base.endswith(".sln") or not any(f.endswith(".sln") and posixpath.dirname(f) == root for f in files):
                entry = _toolchain("dotnet", path, "mcr.microsoft.com/dotnet/sdk:8.0", ["dotnet test"],
                                   lockfile=_sib(path, "packages.lock.json", files), egress=NUGET, allow=["dotnet"])
            else:
                continue
        else:
            continue
        key = (entry["root"], entry["name"].split("-")[0])
        if key in seen:
            continue
        seen.add(key)
        found.append(entry)
    return found


def summary(toolchains: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merged view: suggested image (root component first), check commands, egress, allowlist."""
    checks: List[str] = []
    egress: List[str] = []
    allow: List[str] = []
    for tc in toolchains:
        for cmd in tc["checks"]:
            if not tc["root"]:
                checks.append(cmd)
        egress += tc["egress"]
        allow += tc["validation_allowlist"]
    primary = next((tc for tc in toolchains if not tc["root"]), toolchains[0] if toolchains else None)
    return {"toolchains": [tc["name"] for tc in toolchains], "base_image": primary["base_image"] if primary else "",
            "checks": list(dict.fromkeys(checks)), "egress": list(dict.fromkeys(egress)),
            "validation_allowlist": list(dict.fromkeys(allow)),
            "components": [{"name": tc["name"], "root": tc["root"] or ".", "manifest": tc["manifest"],
                            "base_image": tc["base_image"], "checks": tc["checks"]} for tc in toolchains]}


def detect_for_job(job: Any) -> List[Dict[str, Any]]:
    """Toolchains at the job's base commit (``[]`` when the repository is unreadable)."""
    from .repo_config import list_base_files, read_base_file
    return detect_toolchains(list_base_files(job), lambda path: read_base_file(job, path, max_bytes=256 * 1024))


def planner_hint(job: Any) -> str:
    """Short text for the plan prompt: detected toolchains and their usual check commands."""
    try:
        found = detect_for_job(job)
    except Exception:  # noqa: BLE001 - a hint, never a failure
        return ""
    if not found:
        return ""
    lines = ["Detected toolchains (from manifests at the base commit):"]
    for tc in found[:8]:
        where = tc["root"] or "repository root"
        checks = ", ".join(f"`{c}`" for c in tc["checks"]) or "no default check"
        lines.append(f"- {tc['name']} ({tc['manifest']}, {where}): usual checks {checks}")
    lines.append("Prefer these commands for validation when they fit the change; commands for a component "
                 "run with that component as `cwd`.")
    return "\n".join(lines)
