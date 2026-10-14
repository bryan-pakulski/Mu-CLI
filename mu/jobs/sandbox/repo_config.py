"""Repository-declared sandbox settings, read from the job's BASE commit.

``.mucli/jobs.toml``::

    [sandbox]
    allow_paths = ["tests/fixtures/*.key"]      # tracked secret-looking fixtures that may be mounted
    egress = ["proxy.golang.org", "*.crates.io"] # added to Settings → Extra egress once approved
    validation_allowlist = ["./test.sh", "bash scripts/ci.sh", "bazel"]  # extra check command prefixes
    image = "ghcr.io/acme/builder:2"           # or "devcontainer"; needs the user's approval

Everything is read with ``git show <base>:<path>`` from the object database, never from the
worktree a stage edits, so a stage cannot widen its own sandbox. Values are validated here;
anything malformed is dropped (fail closed), never interpreted.

``egress`` widens the network of implement and checks, the stages that hold the agent's
credential, so it needs the user's approval per repository and exact domain list (the
``environment_approvals.json`` store and fingerprints used for repository images, kind
``"egress"``). Until approved it is simply not applied: the job proceeds without it and the
approval is offered (New job sheet / environments preview, and a job event).
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Any, Dict, List, Optional, Tuple

CONFIG_PATH = ".mucli/jobs.toml"
DOMAIN_RE = re.compile(r"(?:\*\.)?[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,62})(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,62}))+")
# A bare shell/launcher would allow any command, so it is only accepted with a script argument.
_BARE_REFUSED = frozenset({"sh", "bash", "zsh", "dash", "fish", "env", "sudo", "su", "eval", "exec", "xargs",
                           "nohup", "timeout", "time", "nice", "command", "busybox", "doas"})
_OPERATORS = (";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "&", "$")


def _git(cwd: str, *args: str, binary: bool = False, timeout: float = 30) -> Optional[Any]:
    try:
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", cwd, *args],
                                capture_output=True, text=not binary, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return None if result.returncode else result.stdout


def base_ref(job: Any) -> Tuple[str, str]:
    """``(git dir to read from, revision)``: the job's worktree (or repository) at its base commit.

    Before a job has a base commit (New job preview) the repository's own HEAD is used: the
    committed state of the user's checkout, still never an agent-edited tree."""
    base = str(getattr(job, "base_sha", "") or "")
    repos = [(getattr(job, "metadata", None) or {}).get("repository_root"), getattr(job, "repository", "")]
    # The worktree's HEAD carries the job's own checkpoints: only its base commit is trusted.
    candidates = [getattr(job, "worktree", ""), *repos] if base else repos
    for candidate in candidates:
        path = str(candidate or "")
        if path and os.path.isdir(path):
            return path, base or "HEAD"
    return "", ""


def read_base_file(job: Any, path: str, *, max_bytes: int = 1024 * 1024) -> Optional[str]:
    cwd, rev = base_ref(job)
    if not cwd or ".." in path.split("/") or path.startswith("/"):
        return None
    text = _git(cwd, "show", f"{rev}:./{path}")
    if text is None or len(text) > max_bytes:
        return None
    return text


def list_base_files(job: Any, *, limit: int = 200_000) -> List[str]:
    """Repository-relative paths tracked at the base commit (``[]`` when unreadable)."""
    cwd, rev = base_ref(job)
    if not cwd:
        return []
    out = _git(cwd, "ls-tree", "-r", "-z", "--name-only", rev, timeout=60)
    if not out:
        return []
    return [p for p in out.split("\0") if p][:limit]


def base_blob_ids(job: Any, paths: List[str]) -> Dict[str, str]:
    """``{path: blob sha}`` at the base commit for the given tracked paths."""
    cwd, rev = base_ref(job)
    if not cwd or not paths:
        return {}
    out = _git(cwd, "ls-tree", "-r", "-z", rev, "--", *paths[:500], timeout=60)
    found: Dict[str, str] = {}
    for item in (out or "").split("\0"):
        meta, _, name = item.partition("\t")
        parts = meta.split()
        if name and len(parts) == 3:
            found[name] = parts[2]
    return found


def tree_id(job: Any, path: str) -> str:
    cwd, rev = base_ref(job)
    if not cwd:
        return ""
    spec = f"{rev}:./{path.strip('/')}" if path.strip("/") not in ("", ".") else f"{rev}^{{tree}}"
    out = _git(cwd, "rev-parse", "--verify", "--quiet", spec)
    return (out or "").strip()


def base_config(job: Any) -> Dict[str, Any]:
    """The base commit's ``.mucli/jobs.toml`` (``{}`` when absent or invalid)."""
    text = read_base_file(job, CONFIG_PATH)
    if not text:
        return {}
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib
    try:
        data = tomllib.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def clean_allow_paths(value: Any) -> Tuple[str, ...]:
    return tuple(v.lstrip("/") for v in _strings(value) if ".." not in v)


# Second-level labels of common two-label public suffixes (``co.uk``, ``com.au``, ...): a
# wildcard directly under one of them would cover every registrant of a country.
_PUBLIC_SLDS = frozenset({"co", "com", "net", "org", "gov", "edu", "ac", "or", "ne", "go", "gob", "nic", "mil", "ltd",
                          "plc", "nom", "info", "biz", "int"})


def egress_domain_ok(domain: str) -> bool:
    """A host name (or ``*.`` + a registrable domain): never an IP literal, a bare TLD wildcard
    (``*.com``) or a wildcard over a public suffix (``*.co.uk``)."""
    if len(domain) > 253 or not DOMAIN_RE.fullmatch(domain):
        return False
    wildcard = domain.startswith("*.")
    labels = (domain[2:] if wildcard else domain).split(".")
    if labels[-1].isdigit() or all(label.isdigit() for label in labels):
        return False  # IPv4 literal (or a numeric "TLD")
    if not labels[-1].isalpha() and not labels[-1].startswith("xn--"):
        return False
    if wildcard:
        if len(labels) < 2:
            return False
        if len(labels) == 2 and labels[0] in _PUBLIC_SLDS and len(labels[1]) == 2:
            return False
    return True


def clean_egress(value: Any) -> Tuple[str, ...]:
    """Domain names only (``*.`` wildcard prefix over a registrable domain allowed; no IP
    literals); model-provider hosts are dropped (models are reached only through the stage
    relay). The egress bridge still checks every connection's port and resolved public address."""
    from .environments import filter_model_hosts
    domains = [v.lower().rstrip(".") for v in _strings(value)]
    domains = [d for d in domains if egress_domain_ok(d)]
    return tuple(dict.fromkeys(filter_model_hosts(domains)))


def egress_fingerprint(domains) -> str:
    """What the user approves for repository egress: the exact (sorted) domain list."""
    import hashlib
    import json
    key = json.dumps(sorted(set(domains)))
    return "egress:" + hashlib.sha1(key.encode()).hexdigest()[:20]


def egress_approval(job: Any) -> Optional[Dict[str, Any]]:
    """The base commit's ``[sandbox] egress`` as an approval request (``approved`` flag set), or
    None when the repository declares none."""
    domains = repository_sandbox(job)["egress"]
    if not domains:
        return None
    from .devcontainer import is_approved
    from .toolchain import job_repository
    repo = job_repository(job)
    fp = egress_fingerprint(domains)
    ref = ", ".join(domains)
    return {"repository": repo, "fingerprint": fp, "kind": "egress", "ref": ref, "origin": "repo-egress",
            "label": f"network egress to {ref} (.mucli/jobs.toml)", "ignored_features": [],
            "domains": list(domains), "approved": bool(repo) and is_approved(repo, fp)}


def approved_repository_egress(job: Any) -> Tuple[str, ...]:
    """Repository egress only once the user approved this exact list for this repository."""
    request = egress_approval(job)
    return tuple(request["domains"]) if request and request["approved"] else ()


def clean_validation_allowlist(value: Any) -> Tuple[str, ...]:
    """Command prefixes (``uv``, ``./test.sh``, ``bash scripts/ci.sh``): no shell operators, and a
    bare shell or launcher only together with the script it runs."""
    out: List[str] = []
    for raw in _strings(value):
        if any(op in raw for op in _OPERATORS) or len(raw) > 200:
            continue
        try:
            tokens = shlex.split(raw)
        except ValueError:
            continue
        if not tokens or (len(tokens) == 1 and tokens[0].rsplit("/", 1)[-1] in _BARE_REFUSED):
            continue
        if any(t.startswith("-") for t in tokens[1:]) and tokens[0] in _BARE_REFUSED:
            continue  # `bash -c` / `env -S` would run arbitrary text
        out.append(" ".join(tokens))
    return tuple(dict.fromkeys(out))


def clean_image(value: Any) -> str:
    from .toolchain import IMAGE_RE
    text = value.strip() if isinstance(value, str) else ""
    if text == "devcontainer":
        return text
    return text if text and IMAGE_RE.fullmatch(text) and not text.startswith("-") else ""


def repository_sandbox(job: Any) -> Dict[str, Any]:
    """Validated ``[sandbox]`` keys from the base commit."""
    raw = base_config(job).get("sandbox") or {}
    if not isinstance(raw, dict):
        raw = {}
    return {"allow_paths": clean_allow_paths(raw.get("allow_paths")), "egress": clean_egress(raw.get("egress")),
            "validation_allowlist": clean_validation_allowlist(raw.get("validation_allowlist")),
            "image": clean_image(raw.get("image"))}
