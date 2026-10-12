"""One stage, executed only inside the shared job runtime image."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from mu.jobs.ui import JobUI
from mu.ui.exceptions import InteractionRequired
from .provider import RelayProvider


class StageUI(JobUI):
    def __init__(self):
        self._variables = {}
        self._responses = []

    def _event(self, event_type, *, payload=None, reason=""):
        print(json.dumps({"event": event_type, "payload": payload or {}, "reason": reason}), flush=True)

    def request_tool_approval(self, **kwargs):
        self._approval_args = kwargs.get("tool_args") or kwargs.get("display_args") or {}
        return super().request_tool_approval(**kwargs)

    def _take_response(self, kind, *, tool_name=""):
        import httpx
        transport = httpx.HTTPTransport(uds=os.environ["MUCLI_MODEL_RELAY"])
        with httpx.Client(transport=transport, timeout=15, trust_env=False) as client:
            response = client.post("http://relay/interaction", headers={"X-Mucli-Stage-Token": os.environ["MUCLI_STAGE_TOKEN"]},
                                   json={"kind": kind, "tool_name": tool_name,
                                         "tool_args": getattr(self, "_approval_args", {})})
            response.raise_for_status()
            return response.json().get("response")


def main():
    import mucli
    from .egress import start_container_proxy
    start_container_proxy()
    request = json.loads(Path("/artifacts/request.json").read_text())
    stage = os.environ["MUCLI_JOB_STAGE"]
    ui = StageUI()
    model = request["model"]
    # A process-local provider factory: no other process or interactive session
    # can accidentally inherit the relay adapter.
    mucli.init_provider = lambda *a, **k: RelayProvider(model["provider"], model["model"])
    args = SimpleNamespace(session="stage", provider=model["provider"], model=model["model"],
        provider_prevalidated=True, session_type="workspace", workspace=["/workspace"],
        yolo=bool(request.get("auto_approve_writes")) and stage == "implement", gui=False,
        trace=True, debug=False, system=f"You are the {stage} stage of a durable engineering job. "
        "The host owns git commits and publishing. Do not edit .git. "
        "Stage input artifacts are under /artifacts. " + ("Read-only tools only." if stage != "implement" else ""),
        system_file=None, mode_prompt=None)
    session = None
    result = {"kind": "failed", "status": "error"}
    try:
        session = mucli.build_session(args, ui, allow_prompt=False)
        session.variables.update(provider_recovery_policy="auto", internal_session_kind="durable_job",
            max_iterations=int(request.get("max_iterations") or 32), yolo=args.yolo,
            plan_mode=stage != "implement", security_allow_secret_paths=False)
        if stage != "implement":
            from mu.jobs.stages import STAGE_TOOL_ALLOWLIST
            from mu.tools.descriptors import TOOLS
            session.disabled_tools = sorted({t.name for t in TOOLS} - STAGE_TOOL_ALLOWLIST)
        raw = session.send_message(request["prompt"]) or {}
        status = raw.get("status", "completed") if isinstance(raw, dict) else "completed"
        result = {"kind": "completed" if status == "completed" else "needs_human",
                  "status": status, "error": str(raw.get("error") or "") if isinstance(raw, dict) else ""}
        history = session.session_manager.history
        result["text"] = next(("\n".join(p.get("text", "") for p in m.get("parts", [])
                                        if p.get("type") == "text")
                               for m in reversed(history) if m.get("role") == "assistant"), "")
    except InteractionRequired as exc:
        result = {"kind": "needs_human", "status": "needs_input", "error": exc.detail,
                  "attention_reason": "approval_required" if exc.kind == "approval_required" else "question",
                  "attention_payload": exc.payload}
    except Exception as exc:
        result = {"kind": "failed", "status": "error", "error": str(exc)}
    finally:
        if session is not None:
            Path("/output/transcript.json").write_text(json.dumps(session.session_manager.history, default=str))
            session.shutdown()
        Path("/output/result.json").write_text(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
