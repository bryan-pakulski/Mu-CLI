"""Persistent Jira link settings: ``$MUCLI_HOME/integrations/jira.json``.

The file holds the link (base URL, email, API token or PAT) plus non-secret
browsing preferences (preferred boards, default JQL). It is written
atomically with mode 0600 inside a 0700 directory.

Precedence per field: environment variable > settings file. Secrets are
never returned by :func:`public_settings`; callers only see ``*_set`` flags.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional

from .jira import JiraConfig, JiraError

_LOCK = threading.Lock()
SECRET_FIELDS = ("api_token", "pat")
_LINK_FIELDS = ("base_url", "email", "api_token", "pat")
_ENV = {
    "base_url": "JIRA_BASE_URL",
    "email": "JIRA_EMAIL",
    "api_token": "JIRA_API_TOKEN",
    "pat": "JIRA_PAT",
}
_MAX_BOARDS = 50


def settings_path() -> str:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
    return os.path.join(root, "integrations", "jira.json")


def load_settings() -> Dict[str, Any]:
    path = settings_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise JiraError(f"cannot read Jira settings at {path}: {exc.__class__.__name__}") from exc
    return data if isinstance(data, dict) else {}


def _write(data: Dict[str, Any]) -> None:
    path = settings_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".jira.", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _clean_boards(value: Any) -> List[Dict[str, Any]]:
    boards: List[Dict[str, Any]] = []
    seen = set()
    for item in value or []:
        if isinstance(item, (int, str)):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        try:
            board_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if board_id in seen:
            continue
        seen.add(board_id)
        boards.append({
            "id": board_id,
            "name": str(item.get("name") or "")[:200],
            "type": str(item.get("type") or "")[:40],
        })
        if len(boards) >= _MAX_BOARDS:
            break
    return boards


def save_settings(update: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *update* into the stored settings.

    * Blank/absent secret fields keep the stored value (the GUI never
      receives secrets, so it cannot echo them back).
    * ``clear_api_token`` / ``clear_pat`` explicitly remove a secret.
    """
    update = dict(update or {})
    with _LOCK:
        data = load_settings()
        for field in ("base_url", "email"):
            if field in update:
                value = str(update.get(field) or "").strip()
                if field == "base_url":
                    value = value.rstrip("/")
                    if value and not value.startswith(("https://", "http://")):
                        raise ValueError("base_url must start with https://")
                data[field] = value
        for field in SECRET_FIELDS:
            if update.get(f"clear_{field}"):
                data.pop(field, None)
            value = str(update.get(field) or "").strip()
            if value:
                data[field] = value
        prefs = dict(data.get("preferences") or {})
        incoming = update.get("preferences")
        if isinstance(incoming, dict):
            if "boards" in incoming:
                prefs["boards"] = _clean_boards(incoming.get("boards"))
            if "default_jql" in incoming:
                prefs["default_jql"] = str(incoming.get("default_jql") or "").strip()[:2000]
            if "assigned_to_me" in incoming:
                prefs["assigned_to_me"] = bool(incoming.get("assigned_to_me"))
        data["preferences"] = prefs
        _write(data)
    return public_settings()


def clear_settings(*, keep_preferences: bool = True) -> Dict[str, Any]:
    with _LOCK:
        data = load_settings()
        for field in _LINK_FIELDS:
            data.pop(field, None)
        if not keep_preferences:
            data.pop("preferences", None)
        _write(data)
    return public_settings()


def resolve_config(env: Optional[Dict[str, str]] = None) -> JiraConfig:
    """Env-over-file JiraConfig used by the client, router and TUI."""
    e = os.environ if env is None else env
    try:
        stored = load_settings()
    except JiraError:
        stored = {}
    env_cfg = JiraConfig.from_env(e)
    return JiraConfig(
        base_url=(env_cfg.base_url or str(stored.get("base_url") or "")).strip().rstrip("/"),
        email=env_cfg.email or str(stored.get("email") or "").strip(),
        api_token=env_cfg.api_token or str(stored.get("api_token") or "").strip(),
        pat=env_cfg.pat or str(stored.get("pat") or "").strip(),
        writeback=env_cfg.writeback,
    )


def public_settings(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Secret-free view for API responses."""
    e = os.environ if env is None else env
    try:
        stored = load_settings()
        error = ""
    except JiraError as exc:
        stored, error = {}, str(exc)
    cfg = resolve_config(e)
    sources = {}
    for field, var in _ENV.items():
        if str(e.get(var) or "").strip():
            sources[field] = "env"
        elif str(stored.get(field) or "").strip():
            sources[field] = "file"
        else:
            sources[field] = ""
    prefs = dict(stored.get("preferences") or {})
    return {
        "base_url": cfg.base_url,
        "email": cfg.email,
        "mode": cfg.mode,
        "configured": bool(cfg.base_url and cfg.mode),
        "api_token_set": bool(cfg.api_token),
        "pat_set": bool(cfg.pat),
        "writeback": cfg.writeback,
        "sources": sources,
        "settings_path": settings_path(),
        "preferences": {
            "boards": _clean_boards(prefs.get("boards")),
            "default_jql": str(prefs.get("default_jql") or ""),
            "assigned_to_me": bool(prefs.get("assigned_to_me", False)),
        },
        "error": error,
    }
