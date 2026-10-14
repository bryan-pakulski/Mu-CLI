"""Repository intake for jobs: clone (or refresh) a remote repository by URL.

A job may name any repository by URL (``https://``, ``ssh://``, scp-style
``git@host:org/repo.git``, ``file://``), optionally suffixed with ``@branch`` or
``#branch``. The repository is cloned once into
``$MUCLI_HOME/repos/<host>/<org>/<name>-<sha8(url)>`` (partial clone,
``--filter=blob:none``) and refreshed with ``fetch --prune`` on later use.

Authentication is the host's own: git credential helpers and the SSH agent,
strictly non-interactive (``GIT_TERMINAL_PROMPT=0``, ssh ``BatchMode``).
Credentials embedded in a URL are refused — they would otherwise land in the
clone's ``remote.origin.url`` and from there in job records — and every error
message is redacted.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

URL_SCHEMES = ("https", "http", "ssh", "git", "file")
_SCP = re.compile(r"^(?P<user>[A-Za-z0-9._~-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>(?!//).+)$")
_AUTH_HINTS = ("authentication failed", "could not read username", "could not read password",
               "permission denied", "publickey", "host key verification failed", "terminal prompts disabled",
               "access denied", "invalid username or password", "error: 403", "error: 401")
_NOT_FOUND_HINTS = ("repository not found", "not found", "does not appear to be a git repository",
                    "does not exist")


class CloneError(ValueError):
    """Clone/refresh failure with an actionable, credential-free message."""

    def __init__(self, message: str, *, kind: str = "clone_failed", host: str = ""):
        super().__init__(redact(message))
        self.kind = kind
        self.host = host


@dataclass(frozen=True)
class RepoUrl:
    url: str            # credential-free clone URL (no branch suffix)
    branch: str         # requested branch ('' = remote default)
    host: str           # 'github.com', 'local' for file://
    org: str            # owner/group path ('' when none)
    name: str           # repository name without .git

    @property
    def display(self) -> str:
        return redact(self.url)


@dataclass
class CloneResult:
    path: str
    url: str                       # redacted URL (safe for job records)
    branch: str                    # requested or remote default branch
    base_sha: str                  # tip of origin/<branch> after clone/fetch
    created: bool
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {"path": self.path, "url": self.url, "branch": self.branch, "base_sha": self.base_sha,
                "created": self.created, "warnings": list(self.warnings)}


def redact(text: object) -> str:
    """Strip userinfo secrets from any URL in ``text`` (``https://u:t@h`` -> ``https://***@h``)."""
    value = str(text or "")
    value = re.sub(r"(?i)\b([a-z][a-z0-9+.-]*://)[^@/\s]+@", r"\1***@", value)
    return value


def looks_like_url(value: str) -> bool:
    text = str(value or "").strip()
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*://", text):
        return True
    return bool(_SCP.match(text))


def _sanitize(segment: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", segment).strip("-.") or "repo"


def parse_repo_url(value: str) -> RepoUrl:
    """Parse a repository URL with an optional ``@branch`` / ``#branch`` suffix."""
    text = str(value or "").strip()
    if not text:
        raise CloneError("repository URL is empty", kind="invalid_url")
    branch = ""
    if "#" in text:
        text, branch = text.rsplit("#", 1)
    scp = _SCP.match(text)
    if scp:
        path = scp.group("path")
        if "@" in path:
            path, at_branch = path.rsplit("@", 1)
            branch = branch or at_branch
        host = scp.group("host").lower()
        url = f"{scp.group('user')}@{host}:{path}"
    else:
        parts = urlsplit(text)
        scheme = parts.scheme.lower()
        if scheme not in URL_SCHEMES:
            raise CloneError(f"unsupported repository URL scheme {scheme or '(none)'!r}; "
                             f"use one of {', '.join(URL_SCHEMES)} or user@host:path", kind="invalid_url")
        path = parts.path
        if "@" in path:
            path, at_branch = path.rsplit("@", 1)
            branch = branch or at_branch
        netloc = parts.netloc
        if "@" in netloc:
            userinfo, hostport = netloc.rsplit("@", 1)
            # Any userinfo on http(s) is a credential or a token used as the user name
            # (``https://ghp_xxx@github.com/...``) and would be stored in remote.origin.url;
            # ssh:// users (``ssh://git@host``) carry no secret. git:// has no auth at all.
            if scheme in ("https", "http", "git") or (":" in userinfo and scheme != "ssh"):
                raise CloneError(
                    f"credentials in repository URLs are not accepted ({redact(text)}); configure a git "
                    f"credential helper for {hostport.lower()} instead", kind="credentials_in_url",
                    host=hostport.lower())
            netloc = f"{userinfo}@{hostport.lower()}"
        else:
            hostport = netloc
            netloc = netloc.lower()
        if parts.query or parts.fragment:
            raise CloneError("repository URL must not contain a query string", kind="invalid_url")
        host = "local" if scheme == "file" else (hostport.lower() or "local")
        url = urlunsplit((scheme, netloc, path, "", ""))
    branch = branch.strip()
    if branch and (branch.startswith("-") or not re.fullmatch(r"[\w./+-]+", branch) or ".." in branch):
        raise CloneError(f"invalid branch name {branch!r}", kind="invalid_url")
    segments = [s for s in re.split(r"[/:]", path.strip("/")) if s and s not in (".", "..")]
    if not segments:
        raise CloneError(f"repository URL has no path: {redact(text)}", kind="invalid_url")
    name = segments[-1]
    if name.endswith(".git"):
        name = name[:-4]
    org = "/".join(segments[:-1][-3:]) if host != "local" else (segments[-2] if len(segments) > 1 else "")
    return RepoUrl(url=url, branch=branch, host=host, org=org, name=name or "repo")


def repos_root() -> str:
    from utils.config import HISTORY_DIR
    return os.path.join(os.path.expanduser(os.getenv("MUCLI_HOME", HISTORY_DIR)), "repos")


def clone_path(repo: RepoUrl, *, root: Optional[str] = None) -> str:
    digest = hashlib.sha256(_normalize(repo.url).encode("utf-8")).hexdigest()[:8]
    parts = [_sanitize(repo.host), *[_sanitize(s) for s in repo.org.split("/") if s], f"{_sanitize(repo.name)}-{digest}"]
    return os.path.join(root or repos_root(), *parts)


def _normalize(url: str) -> str:
    """Comparison form of a clone URL: no trailing slash or ``.git``, lower-case host."""
    text = str(url or "").strip().rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    scp = _SCP.match(text)
    if scp:
        return f"{scp.group('user')}@{scp.group('host').lower()}:{scp.group('path').strip('/')}"
    parts = urlsplit(text)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


# ----------------------------------------------------------------------------- settings

def _setting(path: str, fallback):
    try:
        from .job_settings import rule_or
        return rule_or(path, fallback)
    except Exception:  # noqa: BLE001
        return fallback


def _timeout() -> int:
    from .repository import git_timeout_seconds
    return git_timeout_seconds()


def _clone_timeout() -> int:
    """Clones transfer far more than one local git call: allow 10x the git timeout."""
    return max(60, _timeout() * 10)


def git_env() -> Dict[str, str]:
    """Non-interactive git: host credential helpers / SSH agent only, never a prompt."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    for key in ("GIT_ASKPASS", "SSH_ASKPASS"):
        env.pop(key, None)
    env["GIT_SSH_COMMAND"] = os.environ.get("GIT_SSH_COMMAND") or "ssh -o BatchMode=yes -o ConnectTimeout=20"
    return env


def _run(argv: List[str], *, cwd: Optional[str] = None, timeout: Optional[int] = None,
         skip_smudge: bool = True) -> Tuple[int, str, str]:
    limit = int(timeout or _timeout())
    env = git_env()
    if skip_smudge:
        # LFS content is fetched explicitly (pull_lfs) so a missing git-lfs only warns.
        env["GIT_LFS_SKIP_SMUDGE"] = "1"
    try:
        proc = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=limit,
                              env=env, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(argv[:3])} timed out after {limit}s (raise jobs.git_timeout_seconds)"
    except OSError as exc:
        return 127, "", f"could not run {argv[0]}: {exc}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _git(path: str, *args: str, timeout: Optional[int] = None, skip_smudge: bool = True) -> Tuple[int, str, str]:
    return _run(["git", "-c", "core.hooksPath=/dev/null", "-C", path, *args], timeout=timeout,
                skip_smudge=skip_smudge)


def _failure(action: str, repo: RepoUrl, stderr: str) -> CloneError:
    detail = redact(stderr.strip().splitlines()[-1] if stderr.strip() else "git failed")
    lowered = stderr.lower()
    if "timed out" in lowered:
        return CloneError(f"{action} {repo.display} timed out: {detail}", kind="timeout", host=repo.host)
    if any(h in lowered for h in _AUTH_HINTS):
        how = "ssh agent (ssh-add) / known_hosts" if not repo.url.startswith(("https://", "http://")) \
            else "a git credential helper (e.g. `gh auth setup-git` or `git config credential.helper`)"
        return CloneError(f"{action} {repo.display} failed: authentication was refused; configure git "
                          f"credentials / ssh agent for {repo.host} ({how}) and retry. git said: {detail}",
                          kind="auth_failed", host=repo.host)
    if any(h in lowered for h in _NOT_FOUND_HINTS):
        return CloneError(f"{action} {repo.display} failed: repository not found or not accessible "
                          f"(check the URL, or configure git credentials / ssh agent for {repo.host}). "
                          f"git said: {detail}", kind="not_found", host=repo.host)
    return CloneError(f"{action} {repo.display} failed: {detail}", kind="clone_failed", host=repo.host)


@contextmanager
def _lock(path: str) -> Iterator[None]:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(f"{path}.lock", "a+")
    try:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - non-POSIX
            pass
        yield
    finally:
        handle.close()


def _remote_default_branch(path: str) -> str:
    code, out, _ = _git(path, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    ref = out.strip() if code == 0 else ""
    if ref.startswith("origin/"):
        return ref[len("origin/"):]
    code, out, _ = _git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    return out.strip() if code == 0 else ""


def _verify_existing(path: str, repo: RepoUrl) -> None:
    code, top, _ = _git(path, "rev-parse", "--show-toplevel")
    if code != 0 or os.path.realpath(top.strip()) != os.path.realpath(path):
        raise CloneError(f"refusing to use {path}: it exists but is not a clone of {repo.display} "
                         "(move or delete it and retry)", kind="path_conflict", host=repo.host)
    code, origin, _ = _git(path, "config", "--get", "remote.origin.url")
    if code != 0 or _normalize(origin.strip()) != _normalize(repo.url):
        raise CloneError(f"refusing to use {path}: its origin is {redact(origin.strip()) or '(none)'}, "
                         f"not {repo.display} (move or delete it and retry)", kind="path_conflict", host=repo.host)


def uses_lfs(path: str) -> bool:
    try:
        with open(os.path.join(path, ".gitattributes"), encoding="utf-8", errors="replace") as handle:
            return any("filter=lfs" in line for line in handle)
    except OSError:
        return False


def init_submodules(path: str, *, timeout: Optional[int] = None) -> Optional[str]:
    """``git submodule update --init --recursive`` when ``.gitmodules`` exists.

    Returns ``None`` on success or no submodules, else a redacted warning."""
    if not os.path.isfile(os.path.join(path, ".gitmodules")):
        return None
    code, _, err = _git(path, "submodule", "update", "--init", "--recursive",
                        timeout=timeout or _clone_timeout())
    if code != 0:
        detail = redact(err.strip().splitlines()[-1] if err.strip() else "git failed")
        return f"submodules could not be initialised: {detail}"
    return None


def pull_lfs(path: str, *, mode: str = "auto") -> Optional[str]:
    """Fetch LFS objects for the checkout; a warning (never an error) when that is impossible."""
    if str(mode or "auto").lower() in ("off", "false", "no", "never") or not uses_lfs(path):
        return None
    if not shutil.which("git-lfs"):
        return "repository uses Git LFS but git-lfs is not installed; LFS files remain pointer files"
    code, _, err = _git(path, "lfs", "pull", timeout=_clone_timeout(), skip_smudge=False)
    if code != 0:
        detail = redact(err.strip().splitlines()[-1] if err.strip() else "git lfs failed")
        return f"git lfs pull failed: {detail}"
    return None


def ensure_clone(url: str, *, branch: Optional[str] = None, submodules: Optional[bool] = None,
                 lfs: Optional[str] = None, root: Optional[str] = None) -> CloneResult:
    """Clone ``url`` (or refresh an existing clone) and return where it lives.

    The returned ``base_sha`` is the tip of ``origin/<branch>`` after the fetch, so
    a job started from an older clone still starts from the current remote state."""
    repo = parse_repo_url(url)
    wanted = str(branch or repo.branch or "").strip()
    if submodules is None:
        submodules = bool(_setting("jobs.clone.submodules", True))
    if lfs is None:
        lfs = str(_setting("jobs.clone.lfs", "auto") or "auto")
    path = clone_path(repo, root=root)
    warnings: List[str] = []
    with _lock(path):
        created = False
        if os.path.exists(path):
            _verify_existing(path, repo)
            code, _, err = _git(path, "fetch", "--prune", "--quiet", "origin", timeout=_clone_timeout())
            if code != 0:
                raise _failure("fetching", repo, err)
            _git(path, "remote", "set-head", "origin", "--auto")
        else:
            staging = f"{path}.partial-{os.getpid()}"
            shutil.rmtree(staging, ignore_errors=True)
            argv = ["git", "-c", "core.hooksPath=/dev/null", "clone", "--filter=blob:none", "--quiet"]
            if wanted:
                argv += ["--branch", wanted]
            argv += ["--", repo.url, staging]
            code, _, err = _run(argv, timeout=_clone_timeout())
            if code != 0:
                shutil.rmtree(staging, ignore_errors=True)
                raise _failure("cloning", repo, err)
            os.replace(staging, path)
            created = True
        branch_name = wanted or _remote_default_branch(path) or "main"
        code, sha, _ = _git(path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch_name}^{{commit}}")
        if code != 0 or not sha.strip():
            raise CloneError(f"branch {branch_name!r} does not exist on {repo.display}", kind="branch_not_found",
                             host=repo.host)
        if submodules:
            note = init_submodules(path)
            if note:
                warnings.append(note)
        note = pull_lfs(path, mode=lfs)
        if note:
            warnings.append(note)
    return CloneResult(path=path, url=repo.display, branch=branch_name, base_sha=sha.strip(),
                       created=created, warnings=warnings)


def resolve_repository(value: str) -> Tuple[str, Optional[CloneResult]]:
    """``(workspace_root, clone)`` for a job's ``repository`` input: URL -> clone, else a local path."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("repository is empty")
    if looks_like_url(text):
        result = ensure_clone(text)
        return result.path, result
    return os.path.abspath(os.path.expanduser(text)), None


def apply_repository(payload: Dict[str, object]) -> Dict[str, object]:
    """Resolve a job payload's ``repository``/``repo`` (URL or local path) into
    ``workspace_root`` (+ for URLs: ``base_branch``/``base_sha`` of the fetched remote tip
    and ``metadata.repository_url`` / ``repository_clone`` / ``repository_warnings``).

    Idempotent; the returned payload no longer carries ``repository``/``repo``."""
    value = dict(payload or {})
    requested = str(value.pop("repository", "") or value.pop("repo", "") or "").strip()
    value.pop("repo", None)
    if not requested:
        return value
    root, clone = resolve_repository(requested)
    current = str(value.get("workspace_root") or "").strip()
    if current and os.path.realpath(os.path.expanduser(current)) != os.path.realpath(root):
        raise ValueError("pass either 'repository' or 'workspace_root', not two different locations")
    value["workspace_root"] = root
    if clone is not None:
        metadata = dict(value.get("metadata") or {})  # type: ignore[arg-type]
        metadata["repository_url"] = clone.url
        metadata["repository_clone"] = {"path": clone.path, "branch": clone.branch, "fetched_sha": clone.base_sha}
        if clone.warnings:
            metadata["repository_warnings"] = list(clone.warnings)
        value["metadata"] = metadata
        if not str(value.get("base_branch") or "").strip() and not str(value.get("base_sha") or "").strip():
            # The clone's checkout may be stale; start from the fetched remote tip.
            value["base_branch"], value["base_sha"] = clone.branch, clone.base_sha
    return value
