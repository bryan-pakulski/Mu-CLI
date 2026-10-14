"""Repository-declared build environments: ``[sandbox] image`` and devcontainer.json.

Sources, all read from the job's BASE commit (never the worktree a stage edits):

* ``.mucli/jobs.toml`` ``[sandbox] image = "<ref>"``: a local image used as the base of the
  job's build environment (job sandboxes never pull);
* ``image = "devcontainer"``, or (when nothing else configures the job) an approved
  ``.devcontainer/devcontainer.json`` / ``.devcontainer.json``: its ``image``, or its
  ``build.dockerfile`` + ``build.context`` (+ ``args``, ``target``), built from a ``git archive``
  of the base commit. Devcontainer *features*, ``dockerComposeFile`` and lifecycle commands
  (``postCreateCommand`` …) are not supported: features are ignored (reported), compose files
  make the devcontainer unusable here.

Approval: a repository decides what runs as the job's environment, and a Dockerfile build runs
its ``RUN`` steps on the host's container engine with network access. So every
repository-declared image or build needs the user's explicit approval, recorded per repository
and *fingerprint* (image ref, or the Dockerfile blob + context tree ids at the base commit):
change the Dockerfile and it needs approving again. Unapproved, an explicit ``[sandbox] image``
parks the job (``EnvironmentBlocked`` carrying ``.approval``); an implicit devcontainer is only
suggested.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DEVCONTAINER_PATHS = (".devcontainer/devcontainer.json", ".devcontainer.json")
ARG_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
TARGET_RE = re.compile(r"[A-Za-z0-9][\w.-]{0,63}")
BUILD_TIMEOUT_S = 3600


# ------------------------------------------------------------------ JSONC


def strip_jsonc(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments (outside strings) and trailing commas."""
    out: List[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    cleaned = "".join(out)
    # Trailing commas: `,` followed only by whitespace before `}` or `]` (strings already intact,
    # but a string could contain ",]": re-scan with the same string awareness).
    result: List[str] = []
    in_string = False
    i, n = 0, len(cleaned)
    while i < n:
        ch = cleaned[i]
        if in_string:
            result.append(ch)
            if ch == "\\" and i + 1 < n:
                result.append(cleaned[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            j = i + 1
            while j < n and cleaned[j] in " \t\r\n":
                j += 1
            if j < n and cleaned[j] in "}]":
                i += 1
                continue
        result.append(ch)
        i += 1
    return "".join(result)


def parse_jsonc(text: str) -> Dict[str, Any]:
    data = json.loads(strip_jsonc(text))
    if not isinstance(data, dict):
        raise ValueError("devcontainer.json must be an object")
    return data


# ------------------------------------------------------------------ spec


def _inside(base_dir: str, relative: str) -> Optional[str]:
    """Normalised repository-relative path of ``relative`` (from ``base_dir``), None if it escapes."""
    rel = str(relative or "").replace("\\", "/")
    if rel.startswith("/"):
        return None
    path = posixpath.normpath(posixpath.join(base_dir, rel)) if base_dir else posixpath.normpath(rel)
    if path == ".." or path.startswith("../"):
        return None
    return "" if path == "." else path


def devcontainer_spec(job: Any) -> Optional[Dict[str, Any]]:
    """The base commit's devcontainer as ``{kind: image|build, ...}``; None when absent.

    ``{"error": ...}`` when present but unusable here (compose, invalid, escaping paths)."""
    from .repo_config import read_base_file
    from .toolchain import IMAGE_RE
    for path in DEVCONTAINER_PATHS:
        text = read_base_file(job, path, max_bytes=256 * 1024)
        if text is None:
            continue
        try:
            data = parse_jsonc(text)
        except ValueError as exc:
            return {"file": path, "error": f"{path} is not valid JSON(C): {exc}"}
        features = sorted((data.get("features") or {}).keys()) if isinstance(data.get("features"), dict) else []
        base = {"file": path, "ignored_features": features}
        if data.get("dockerComposeFile"):
            return {**base, "error": f"{path} uses dockerComposeFile, which job sandboxes do not support"}
        build = data.get("build") if isinstance(data.get("build"), dict) else {}
        dockerfile = build.get("dockerfile") or build.get("dockerFile") or data.get("dockerFile")
        here = posixpath.dirname(path)
        if dockerfile:
            df = _inside(here, str(dockerfile))
            context = _inside(here, str(build.get("context") or data.get("context") or "."))
            if df is None or context is None or not df:
                return {**base, "error": f"{path}: build paths must stay inside the repository"}
            args = build.get("args") if isinstance(build.get("args"), dict) else {}
            clean_args = {}
            for key, value in args.items():
                if not ARG_RE.fullmatch(str(key)) or not isinstance(value, (str, int, float, bool)):
                    return {**base, "error": f"{path}: unsupported build arg {key!r}"}
                if "${" in str(value):
                    return {**base, "error": f"{path}: build arg {key} uses a ${{...}} variable, which is not supported"}
                clean_args[str(key)] = str(value)
            target = str(build.get("target") or "")
            if target and not TARGET_RE.fullmatch(target):
                return {**base, "error": f"{path}: invalid build target {target!r}"}
            return {**base, "kind": "build", "dockerfile": df, "context": context, "args": clean_args, "target": target}
        image = str(data.get("image") or "").strip()
        if image:
            if not IMAGE_RE.fullmatch(image) or image.startswith("-"):
                return {**base, "error": f"{path}: invalid image reference {image!r}"}
            return {**base, "kind": "image", "image": image}
        return {**base, "error": f"{path} names neither an image nor a Dockerfile build"}
    return None


def fingerprint(job: Any, spec: Dict[str, Any]) -> str:
    """What the user approves: an image ref, or the exact Dockerfile + context + args."""
    if spec.get("kind") == "image":
        return f"image:{spec['image']}"
    from .repo_config import base_blob_ids, tree_id
    blob = base_blob_ids(job, [spec["dockerfile"]]).get(spec["dockerfile"], "")
    tree = tree_id(job, spec["context"])
    if not blob or not tree:
        return ""
    key = json.dumps({"dockerfile": spec["dockerfile"], "blob": blob, "context": spec["context"], "tree": tree,
                      "args": spec.get("args") or {}, "target": spec.get("target") or ""}, sort_keys=True)
    return "build:" + hashlib.sha1(key.encode()).hexdigest()[:20]


def build_tag(job: Any, fp: str) -> str:
    from .cache import repository_identity
    repo = re.sub(r"[^a-z0-9_.-]+", "-", (repository_identity(job) or "repo").lower()).strip("-.") or "repo"
    return f"mucli/devcontainer-{repo[:60]}:{fp.split(':', 1)[-1][:20]}"


# ------------------------------------------------------------------ approvals


def approvals_path() -> Path:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
    return Path(root) / "jobs" / "environment_approvals.json"


_lock = threading.Lock()


def _read() -> Dict[str, Any]:
    try:
        data = json.loads(approvals_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _repo_key(repository: str) -> str:
    return os.path.realpath(os.path.expanduser(str(repository or "")))


def is_approved(repository: str, fp: str) -> bool:
    return bool(fp) and fp in (_read().get(_repo_key(repository)) or {})


def approve(repository: str, fp: str, *, kind: str = "", ref: str = "") -> Dict[str, Any]:
    """Record the user's approval (a human action: UI button or terminal command)."""
    if not repository or not fp:
        raise ValueError("an approval needs a repository and a fingerprint")
    with _lock:
        data = _read()
        data.setdefault(_repo_key(repository), {})[fp] = {"kind": kind, "ref": ref, "approved_at": time.time()}
        path = approvals_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".approvals.")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return data[_repo_key(repository)][fp]


def revoke(repository: str, fp: str) -> None:
    with _lock:
        data = _read()
        (data.get(_repo_key(repository)) or {}).pop(fp, None)
        path = approvals_path()
        if path.parent.is_dir():
            path.write_text(json.dumps(data, indent=2, sort_keys=True))


# ------------------------------------------------------------------ resolution


def repository_environment(job: Any, *, implicit_devcontainer: bool = False) -> Optional[Dict[str, Any]]:
    """The repository-declared environment for ``job``.

    ``{"origin", "kind", "ref", "fingerprint", "approved", "spec", "label"}``; ``{"error"}`` when the
    declaration is unusable; None when the repository declares nothing (or, for an implicit
    devcontainer, when ``implicit_devcontainer`` is off)."""
    from .repo_config import repository_sandbox
    from .toolchain import job_repository
    repo = job_repository(job)
    declared = repository_sandbox(job)["image"]
    if declared and declared != "devcontainer":
        fp = f"image:{declared}"
        return {"origin": "repo-image", "kind": "image", "ref": declared, "fingerprint": fp,
                "approved": is_approved(repo, fp), "spec": {"kind": "image", "image": declared},
                "label": f"image {declared} (.mucli/jobs.toml)"}
    if not declared and not implicit_devcontainer:
        return None
    spec = devcontainer_spec(job)
    if spec is None:
        return {"origin": "repo-image", "error": ".mucli/jobs.toml sets image = \"devcontainer\" but the base commit "
                                                 "has no .devcontainer/devcontainer.json"} if declared else None
    if spec.get("error"):
        return {"origin": "devcontainer", "error": spec["error"], "explicit": bool(declared)}
    fp = fingerprint(job, spec)
    if not fp:
        return {"origin": "devcontainer", "error": f"{spec['file']}: cannot read the Dockerfile at the base commit",
                "explicit": bool(declared)}
    ref = spec["image"] if spec["kind"] == "image" else build_tag(job, fp)
    what = f"image {spec['image']}" if spec["kind"] == "image" else f"Dockerfile {spec['dockerfile']} (context {spec['context'] or '.'})"
    return {"origin": "devcontainer", "kind": spec["kind"], "ref": ref, "fingerprint": fp, "spec": spec,
            "approved": is_approved(repo, fp), "explicit": bool(declared),
            "label": f"{what} from {spec['file']}", "ignored_features": spec.get("ignored_features") or []}


def approval_request(job: Any, env: Dict[str, Any]) -> Dict[str, Any]:
    from .toolchain import job_repository
    return {"repository": job_repository(job), "fingerprint": env["fingerprint"], "kind": env["kind"],
            "ref": env["ref"], "label": env["label"], "origin": env["origin"],
            "ignored_features": env.get("ignored_features") or []}


def approval_message(env: Dict[str, Any]) -> str:
    action = "build and use" if env.get("kind") == "build" else "use"
    note = ""
    if env.get("kind") == "build":
        note = (" Building runs the Dockerfile's RUN steps on your container engine with network access; review it "
                "first.")
    if env.get("ignored_features"):
        note += f" Devcontainer features are not supported and will be ignored: {', '.join(env['ignored_features'])}."
    return (f"This repository asks mucli to {action} {env['label']} as the job's build environment. "
            f"Approve it for this repository (Approve environment), or configure an environment yourself.{note}")


# ------------------------------------------------------------------ build


def _safe_extract(archive: str, dest: str) -> None:
    root = os.path.realpath(dest)
    with tarfile.open(archive) as tar:
        members = []
        for member in tar.getmembers():
            target = os.path.realpath(os.path.join(dest, member.name))
            if not (target == root or target.startswith(root + os.sep)):
                raise ValueError(f"unsafe path in archive: {member.name}")
            if member.issym() or member.islnk():
                start = os.path.dirname(target) if member.issym() else dest
                link = os.path.realpath(os.path.join(start, member.linkname))
                if not (link == root or link.startswith(root + os.sep)):
                    continue  # a symlink pointing outside the context is dropped, never followed
            if member.isdev():
                continue
            members.append(member)
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, members=members, filter="data")  # noqa: S202 - members vetted above
        else:  # Python < 3.10.12 / 3.11.4
            tar.extractall(dest, members=members)  # noqa: S202


def _drop_secrets(root: str, allow_paths=()) -> List[str]:
    """Tracked secret-looking files never enter a build context (same rule as the worktree
    pre-check: env templates and the base commit's ``allow_paths`` excepted)."""
    from mu.security.secret_paths import is_denied_path
    from .policy import _allowed, is_env_template
    dropped = []
    for directory, dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(directory, name)
            rel = os.path.relpath(path, root)
            if is_denied_path(path)[0] and not is_env_template(name) and not _allowed(rel, allow_paths):
                os.unlink(path)
                dropped.append(rel)
    return dropped


def build_devcontainer(job: Any, spec: Dict[str, Any], tag: str, labels: Dict[str, str], *,
                       run: Callable[..., Any] = subprocess.run, log: Callable[[str], None] = lambda text: None) -> str:
    """Build the devcontainer Dockerfile from a ``git archive`` of the base commit (never the worktree)."""
    from .cli import container_cli
    from .repo_config import base_ref
    cwd, rev = base_ref(job)
    if not cwd or not str(getattr(job, "base_sha", "") or ""):
        raise RuntimeError("the devcontainer is built from the job's base commit, which is not known yet")
    with tempfile.TemporaryDirectory(prefix="mucli-devcontainer-") as tmp:
        archive = os.path.join(tmp, "src.tar")
        result = run(["git", "-c", "core.hooksPath=/dev/null", "-C", cwd, "archive", "--format=tar", "-o", archive, rev],
                     capture_output=True, text=True, timeout=600)
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError(f"git archive failed: {(getattr(result, 'stderr', '') or '').strip()[:300]}")
        src = os.path.join(tmp, "src")
        os.mkdir(src)
        _safe_extract(archive, src)
        from .repo_config import repository_sandbox
        dropped = _drop_secrets(src, repository_sandbox(job)["allow_paths"])
        if dropped:
            log(f"Left {len(dropped)} secret-looking file(s) out of the build context: {', '.join(dropped[:5])}\n")
        context = os.path.join(src, spec["context"]) if spec["context"] else src
        dockerfile = os.path.join(src, spec["dockerfile"])
        if not os.path.isfile(dockerfile) or os.path.islink(dockerfile):
            raise RuntimeError(f"{spec['dockerfile']} is missing at the base commit")
        command = [container_cli(), "build", "--pull=false", "-f", dockerfile, "-t", tag]
        for key, value in sorted(labels.items()):
            command += ["--label", f"{key}={value}"]
        for key, value in sorted((spec.get("args") or {}).items()):
            command += ["--build-arg", f"{key}={value}"]
        if spec.get("target"):
            command += ["--target", spec["target"]]
        log(f"Building the repository's devcontainer ({spec['dockerfile']}) as {tag} …\n")
        result = run([*command, context], capture_output=True, text=True, timeout=BUILD_TIMEOUT_S)
        log(str(getattr(result, "stdout", "") or "")[-4000:])
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError(f"devcontainer build failed: {(getattr(result, 'stderr', '') or '').strip()[-600:]}")
    return tag


def build_args_from(binding: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """``(spec, fingerprint)`` stored on a binding (``build`` pairs) for snapshot_binding."""
    pairs = dict(binding.get("build") or ())
    spec = json.loads(pairs.get("spec") or "{}")
    return spec, str(pairs.get("fingerprint") or "")
