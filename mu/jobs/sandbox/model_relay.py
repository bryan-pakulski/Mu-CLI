"""Stage-scoped streaming relay. Provider credentials live only in this process."""
from __future__ import annotations

import base64
import dataclasses
import json
import secrets
import threading
import time
from pathlib import Path

from fastapi import Request

from providers.base import CacheHint, FileReference, ImageData, MediaData, Message, MessagePart, ToolDefinition


def wire(value):
    if dataclasses.is_dataclass(value):
        return {f.name: wire(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (tuple, list)):
        return [wire(v) for v in value]
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    return value


def messages_from_wire(values):
    def media(raw, cls=MediaData):
        return cls(**{**raw, "data": base64.b64decode(raw["data"]["base64"], validate=True)})
    result = []
    for value in values:
        parts = []
        for item in value.get("parts", []):
            p = dict(item)
            if p.get("inline_data"):
                p["inline_data"] = base64.b64decode(p["inline_data"]["base64"], validate=True)
            if p.get("image"):
                p["image"] = media(p["image"], ImageData)
            if p.get("media"):
                p["media"] = media(p["media"])
            if p.get("file_ref"):
                p["file_ref"] = FileReference(**p["file_ref"])
            p["media_inputs"] = [media(m) for m in p.get("media_inputs", [])]
            parts.append(MessagePart(**p))
        result.append(Message(role=value["role"], parts=parts))
    return result


class RelayDenied(RuntimeError):
    pass


class ModelRelay:
    def __init__(self, job, stage, model, policy, artifacts, *, provider_factory=None, alive=None):
        self.job, self.stage, self.model = job, stage, dict(model)
        if getattr(job, "max_cost_usd", None) is not None:
            policy = dataclasses.replace(policy, max_cost_usd=min(policy.max_cost_usd,
                max(0, float(job.max_cost_usd) - float(job.cost_usd or 0))))
        self.policy, self.artifacts = policy, artifacts
        self.token = secrets.token_urlsafe(32)
        self.revoked = False
        self.tokens = 0
        self.cost = 0.0
        self.lock = threading.Lock()
        self.alive = alive or (lambda: True)
        if provider_factory is None:
            from mucli import init_provider
            provider_factory = init_provider
        self.provider = provider_factory(model["provider"], model["model"])
        if self.provider is None:
            raise ValueError("stage provider is not configured")

    def authorize(self, token):
        if self.revoked or not secrets.compare_digest(str(token or ""), self.token) or not self.alive():
            raise RelayDenied("stage token is invalid, revoked, or no longer owns the job")

    def stream(self, token, payload):
        self.authorize(token)
        if not self.lock.acquire(blocking=False):
            raise RelayDenied("only one model request per stage may run at a time")
        try:
            self.authorize(token)
            if any(k in payload for k in ("provider", "model", "url", "api_key")):
                raise RelayDenied("the host fixes the stage provider and model")
            if self.tokens >= self.policy.max_tokens or self.cost >= self.policy.max_cost_usd:
                raise RelayDenied("stage token/cost budget exhausted")
            messages = messages_from_wire(payload.get("messages", []))
            from utils.token_estimator import estimate_tokens
            request_tokens = estimate_tokens(json.dumps(payload))
            if request_tokens + self.tokens >= self.policy.max_tokens:
                raise RelayDenied("request would exceed the stage token budget")
            from utils.model_pricing import estimate_model_cost
            def projected_cost(output):
                value = estimate_model_cost(provider=self.model["provider"], model_name=self.model["model"],
                                            input_tokens=request_tokens, output_tokens=output)
                return value.get("api_cost_usd")
            request_cost = projected_cost(0)
            if request_cost is not None and self.cost + request_cost >= self.policy.max_cost_usd:
                raise RelayDenied("request would exceed the stage cost budget")
            tools = [ToolDefinition(**t) for t in payload.get("tools") or []]
            hint = CacheHint(**payload["cache_hint"]) if payload.get("cache_hint") else None
            self.provider.model_name = self.model["model"]
            usage = None
            emitted_tokens = 0
            try:
                for event in self.provider.stream(messages, system_prompt=payload.get("system_prompt"),
                                                  thinking=bool(payload.get("thinking")), tools=tools,
                                                  cache_hint=hint, reasoning_effort=payload.get("reasoning_effort")):
                    self.authorize(token)
                    if event.text:
                        emitted_tokens += estimate_tokens(event.text)
                    if self.tokens + request_tokens + emitted_tokens >= self.policy.max_tokens:
                        raise RelayDenied("stage token budget exhausted during response")
                    estimate = projected_cost(emitted_tokens)
                    if estimate is not None and self.cost + estimate >= self.policy.max_cost_usd:
                        raise RelayDenied("stage cost budget exhausted during response")
                    if event.kind == "usage":
                        usage = event
                    yield wire(event)
            finally:
                if usage is not None:
                    from utils.model_pricing import estimate_model_cost
                    price = estimate_model_cost(provider=self.model["provider"], model_name=self.model["model"],
                        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                        cached_tokens=usage.cached_tokens, reasoning_tokens=usage.reasoning_tokens)
                    cost = price.get("api_cost_usd")
                    self.tokens += usage.total_tokens or usage.input_tokens + usage.output_tokens
                    self.cost += float(cost or 0)
                    self.artifacts.append_cost({"stage": self.stage, "at": time.time(),
                        "provider": self.model["provider"], "model": self.model["model"],
                        "cost_usd": cost, "tokens": wire(usage)})
        finally:
            self.lock.release()

    def app(self):
        from fastapi import FastAPI, Header, HTTPException, Request
        from fastapi.responses import StreamingResponse
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.post("/stream")
        async def stream(request: Request, x_mucli_stage_token: str = Header(default="")):
            try:
                self.authorize(x_mucli_stage_token)
            except RelayDenied as exc:
                raise HTTPException(403, str(exc)) from exc
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 16 * 1024 * 1024:
                    raise HTTPException(413, "model request is too large")
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError()
            except ValueError as exc:
                raise HTTPException(400, "invalid model request") from exc

            def events():
                try:
                    for event in self.stream(x_mucli_stage_token, payload):
                        yield json.dumps(event) + "\n"
                except Exception:
                    # Provider exceptions can contain request headers or keys.
                    yield json.dumps({"kind": "error", "text": "Stage model relay failed; inspect host diagnostics."}) + "\n"
            return StreamingResponse(events(), media_type="application/x-ndjson")
        return app

    def start(self, directory):
        import uvicorn
        socket = Path(directory) / "model.sock"
        self.server = uvicorn.Server(uvicorn.Config(self.app(), uds=str(socket), log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                self.close()
                raise RuntimeError("job model relay failed to start")
            time.sleep(.02)
        socket.chmod(0o600)
        return self

    def close(self):
        self.revoked = True
        if hasattr(self, "server"):
            self.server.should_exit = True
        if hasattr(self, "thread"):
            self.thread.join(timeout=3)
