"""PR/MR provider tokens for publishing (GitHub, GitLab, Bitbucket, Gitea/Forgejo).

Publishing reads tokens from the environment first, then from this store:
``$MUCLI_HOME/jobs/publish_tokens.env`` (0600, atomic writes). The store is
separate from the agent-harness ``secrets.env`` on purpose: nothing here is
ever passed into a job container. Only the host-side publish step (push + open
PR/MR + merge polling) reads it. Values are never returned by the API.
"""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Dict, Optional

# provider → environment variable names, first one is the one we store.
TOKEN_VARS: Dict[str, tuple] = {
    "github": ("GITHUB_TOKEN", "GH_TOKEN"),
    "gitlab": ("GITLAB_TOKEN",),
    "bitbucket": ("BITBUCKET_TOKEN",),
    "gitea": ("GITEA_TOKEN",),
}
_KEYS = {names[0] for names in TOKEN_VARS.values()}
_lock = threading.Lock()


def tokens_path() -> str:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
    return os.path.join(root, "jobs", "publish_tokens.env")


def read_tokens() -> Dict[str, str]:
    """Stored tokens as ``{ENV_NAME: value}`` (unknown keys ignored)."""
    out: Dict[str, str] = {}
    try:
        with open(tokens_path(), "r", encoding="utf-8") as handle:
            for line in handle:
                key, sep, value = line.strip().partition("=")
                if sep and key in _KEYS:
                    out[key] = value.strip().strip("'\"")
    except OSError:
        pass
    return out


def write_token(provider: str, value: Optional[str]) -> None:
    """Store (or with an empty value, clear) one provider's token."""
    provider = str(provider or "").strip().lower()
    if provider not in TOKEN_VARS:
        raise ValueError(f"unknown PR provider {provider!r} (github, gitlab, bitbucket, gitea)")
    key = TOKEN_VARS[provider][0]
    value = str(value or "").strip()
    if any(ch in value for ch in "\n\r'\"") or (value and any(ch.isspace() for ch in value)):
        raise ValueError("tokens cannot contain spaces, quotes or newlines")
    with _lock:
        data = read_tokens()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        path = tokens_path()
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".publish_tokens.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("".join(f"{k}='{v}'\n" for k, v in sorted(data.items())))
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def with_tokens(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """``env`` (default: the process environment) with stored tokens filled in underneath.

    Environment variables always win over stored values."""
    base = dict(os.environ if env is None else env)
    for key, value in read_tokens().items():
        if not base.get(key):
            base[key] = value
    return base


def token_source(provider: str, env: Optional[Dict[str, str]] = None) -> str:
    """``"environment"``, ``"stored"`` or ``""`` for a provider's token."""
    names = TOKEN_VARS.get(str(provider or "").lower(), ())
    live = os.environ if env is None else env
    if any(live.get(name) for name in names):
        return "environment"
    stored = read_tokens()
    return "stored" if any(stored.get(name) for name in names) else ""


def status() -> Dict[str, Dict[str, str]]:
    """``{provider: {"variable": ENV_NAME, "source": environment|stored|""}}`` — never values."""
    return {provider: {"variable": names[0], "source": token_source(provider)}
            for provider, names in TOKEN_VARS.items()}
