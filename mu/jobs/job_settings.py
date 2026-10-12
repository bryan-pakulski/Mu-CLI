"""Durable-job pipeline settings: ``$MUCLI_HOME/jobs/settings.json``.

Holds the default provider/model per pipeline stage (plan, implement, review)
and whether a generated plan must be approved by a human before
implementation starts. Per-job ``execution.stages`` / ``execution.provider``
still win; these settings are the fallback so jobs imported from Jira (which
carry no model) can run.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

from .models import STAGES

_DEFAULTS: Dict[str, Any] = {"stage_models": {}, "plan_approval": True}


def settings_path() -> str:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
    return os.path.join(root, "jobs", "settings.json")


def _clean_stage(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    provider = str(value.get("provider") or "").strip()[:64]
    model = str(value.get("model") or "").strip()[:200]
    if not model:
        return {}
    return {"provider": provider, "model": model}


def load_settings() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    try:
        with open(settings_path(), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            data = raw
    except (OSError, ValueError):
        data = {}
    stages = {}
    for stage in STAGES:
        cleaned = _clean_stage((data.get("stage_models") or {}).get(stage))
        if cleaned:
            stages[stage] = cleaned
    return {"stage_models": stages, "plan_approval": bool(data.get("plan_approval", True)),
            "sandbox": dict(data.get("sandbox") or {}), "checks": dict(data.get("checks") or {})}


def save_settings(update: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(update, dict):
        raise ValueError("settings payload must be an object")
    current = load_settings()
    if "stage_models" in update:
        incoming = update.get("stage_models") or {}
        if not isinstance(incoming, dict):
            raise ValueError("stage_models must be an object")
        unknown = sorted(set(incoming) - set(STAGES))
        if unknown:
            raise ValueError(f"unknown stage(s): {', '.join(unknown)}")
        stages = dict(current["stage_models"])
        for stage, value in incoming.items():
            cleaned = _clean_stage(value)
            if cleaned:
                if not cleaned["provider"]:
                    raise ValueError(f"stage '{stage}': provider is required when a model is set")
                stages[stage] = cleaned
            else:
                stages.pop(stage, None)
        current["stage_models"] = stages
    if "plan_approval" in update:
        current["plan_approval"] = bool(update.get("plan_approval"))
    for key in ("sandbox", "checks"):
        if key in update:
            if not isinstance(update[key], dict):
                raise ValueError(f"{key} must be an object")
            current[key] = {**current.get(key, {}), **update[key]}
    from .sandbox.policy import SandboxPolicy
    for stage in (*STAGES, "checks"):
        SandboxPolicy.for_stage(stage, current)
    path = settings_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return current


def stage_model(stage: str, settings: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    data = settings if settings is not None else load_settings()
    return dict((data.get("stage_models") or {}).get(stage) or {})


def resolve(execution: Optional[Dict[str, Any]], stage: str,
            settings: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """stage override -> job default -> settings for this stage."""
    exe = dict(execution or {})
    cfg = dict((exe.get("stages") or {}).get(stage) or {})
    if cfg.get("model"):
        return {"stage": stage, "provider": str(cfg.get("provider") or exe.get("provider") or ""),
                "model": str(cfg["model"]), "source": "stage"}
    if exe.get("model"):
        return {"stage": stage, "provider": str(exe.get("provider") or ""),
                "model": str(exe["model"]), "source": "job"}
    fallback = stage_model(stage, settings)
    if fallback.get("model"):
        return {"stage": stage, "provider": fallback.get("provider", ""),
                "model": fallback["model"], "source": "settings"}
    return {"stage": stage, "provider": "", "model": "", "source": "unset"}


def missing_stages(execution: Optional[Dict[str, Any]],
                   settings: Optional[Dict[str, Any]] = None) -> List[str]:
    data = settings if settings is not None else load_settings()
    out = []
    for stage in STAGES:
        model = resolve(execution, stage, data)
        if not (model["provider"] and model["model"]):
            out.append(stage)
    return out


def plan_approval_required(execution: Optional[Dict[str, Any]],
                           settings: Optional[Dict[str, Any]] = None) -> bool:
    exe = dict(execution or {})
    if "plan_approval" in exe:
        return bool(exe.get("plan_approval"))
    data = settings if settings is not None else load_settings()
    return bool(data.get("plan_approval", True))


def public_settings() -> Dict[str, Any]:
    data = load_settings()
    missing = missing_stages({}, data)
    return {**data, "stages": list(STAGES), "missing": missing, "configured": not missing}
