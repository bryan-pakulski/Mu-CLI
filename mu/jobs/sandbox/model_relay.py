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


# Charged for a model the pricing table does not know (USD per million tokens). Deliberately
# high (frontier-model list prices) so an unpriced model can never run a job past its budget.
FALLBACK_INPUT_PER_MILLION = 15.0
FALLBACK_CACHED_PER_MILLION = 1.5
FALLBACK_OUTPUT_PER_MILLION = 75.0
_UNPRICED_WARNED: set = set()


def price_usd(provider, model, *, input_tokens=0, output_tokens=0, cached_tokens=0, reasoning_tokens=0):
    """``(cost, fallback)``: the pricing table's estimate, or the conservative fallback price
    when the model is unpriced (``fallback`` True). Local Ollama stays $0."""
    from utils.model_pricing import estimate_model_cost
    value = estimate_model_cost(provider=provider, model_name=model, input_tokens=input_tokens,
                                output_tokens=output_tokens, cached_tokens=cached_tokens,
                                reasoning_tokens=reasoning_tokens).get("api_cost_usd")
    if value is not None:
        return float(value), False
    cached = max(0, min(int(input_tokens or 0), int(cached_tokens or 0)))
    uncached = max(0, int(input_tokens or 0) - cached)
    cost = (uncached * FALLBACK_INPUT_PER_MILLION + cached * FALLBACK_CACHED_PER_MILLION
            + max(0, int(output_tokens or 0)) * FALLBACK_OUTPUT_PER_MILLION) / 1_000_000
    return cost, True


def prior_spend(artifacts, stage):
    """``(this stage's spend, whole job's spend)`` from the job's cost ledger (costs.jsonl)."""
    try:
        totals = artifacts.stage_costs()
    except (OSError, ValueError):
        totals = {}
    return float(totals.get(stage, 0.0)), float(sum(totals.values()))


def prior_tokens(artifacts, stage):
    """Tokens earlier runs of ``stage`` charged against its token budget (``charged_tokens``)."""
    reader = getattr(artifacts, "stage_tokens", None)
    if reader is None:
        return 0
    try:
        return int(reader().get(stage, 0))
    except (OSError, ValueError, TypeError):
        return 0


class ModelRelay:
    def __init__(self, job, stage, model, policy, artifacts, *, provider_factory=None, alive=None, messages=None, interaction=None):
        self.job, self.stage, self.model = job, stage, dict(model)
        # The stage budgets (cost and tokens) cover every run of this stage in the job (resumes,
        # restarts, fix attempts), not each run separately: earlier runs' spend comes off the top.
        stage_spent, job_spent = prior_spend(artifacts, stage)
        self.prior_stage_cost = stage_spent
        self.prior_job_cost = max(float(getattr(job, "cost_usd", 0) or 0), job_spent)
        self.stage_cost_limit = float(policy.max_cost_usd)
        self.prior_stage_tokens = prior_tokens(artifacts, stage)
        self.stage_token_limit = int(policy.max_tokens)
        stage_left = max(0.0, self.stage_cost_limit - stage_spent)
        budget, self.cost_scope = stage_left, "stage"
        cap = getattr(job, "max_cost_usd", None)
        self.job_cost_limit = float(cap) if cap else None  # None / 0 = no job cost cap
        if self.job_cost_limit is not None:
            job_left = max(0.0, self.job_cost_limit - self.prior_job_cost)
            if job_left <= stage_left:
                budget, self.cost_scope = job_left, "job"
        policy = dataclasses.replace(policy, max_cost_usd=budget,
                                     max_tokens=max(0, self.stage_token_limit - self.prior_stage_tokens))
        # The cap this stage reached (``mu.jobs.limits.limit_detail``), set on the first refusal;
        # the stage runner pauses the stage on it instead of failing.
        self.limit_hit = None
        # Called once with a host-side warning (e.g. an unpriced model) — set by the stage runner.
        self.on_warning = None
        self.policy, self.artifacts = policy, artifacts
        self.token = secrets.token_urlsafe(32)
        self.revoked = False
        self.tokens = 0
        self.cost = 0.0
        self.lock = threading.Lock()
        self.alive = alive or (lambda: True)
        self.messages = messages or (lambda: [])
        self.interaction = interaction or (lambda payload: None)
        # Host-side record of relay failures (set by the stage runner). Never sent to the sandbox.
        self.on_error = None
        # run_in_environment broker (set by the stage runner for multi-environment implement stages).
        self.broker = None
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
            if self.tokens >= self.policy.max_tokens:
                raise RelayDenied(self._budget_text("token"))
            if self.cost >= self.policy.max_cost_usd:
                raise RelayDenied(self._budget_text("cost"))
            messages = messages_from_wire(payload.get("messages", []))
            guidance = self.messages()
            messages.extend(Message("user", [MessagePart("text", text=text)]) for text in guidance)
            from utils.token_estimator import estimate_tokens
            request_tokens = estimate_tokens(json.dumps(payload)) + sum(estimate_tokens(text) for text in guidance)
            # Cache reads are not counted by default, so only a request that could not fit even
            # uncached is refused up front; the real (new-token) usage is charged after the call.
            projected = request_tokens + (self.tokens if self.policy.count_cache_reads else 0)
            if projected >= self.policy.max_tokens or (not self.policy.count_cache_reads and self.tokens >= self.policy.max_tokens):
                raise RelayDenied(self._budget_text("token", request_tokens))
            def projected_cost(output):
                return price_usd(self.model["provider"], self.model["model"],
                                 input_tokens=request_tokens, output_tokens=output)[0]
            request_cost = projected_cost(0)
            if request_cost is not None and self.cost + request_cost >= self.policy.max_cost_usd:
                raise RelayDenied(self._budget_text("cost"))
            window = self._context_window()
            if window and request_tokens >= window:
                # cl100k undercounts Claude tokens, so this request is certain to be rejected.
                # The wording matches the loop's overflow detection, which compacts and retries.
                raise RelayDenied(f"prompt is too long: about {request_tokens:,} tokens > {window:,} maximum "
                                  "(refused by the job relay before sending)")
            tools = [ToolDefinition(**t) for t in payload.get("tools") or []]
            hint = CacheHint(**payload["cache_hint"]) if payload.get("cache_hint") else None
            self.provider.model_name = self.model["model"]
            usage = None
            emitted_tokens = 0
            received = False
            try:
                for event in self.provider.stream(messages, system_prompt=payload.get("system_prompt"),
                                                  thinking=bool(payload.get("thinking")), tools=tools,
                                                  cache_hint=hint, reasoning_effort=payload.get("reasoning_effort")):
                    received = True
                    self.authorize(token)
                    if event.text:
                        emitted_tokens += estimate_tokens(event.text)
                    if self.tokens + (request_tokens if self.policy.count_cache_reads else 0) + emitted_tokens >= self.policy.max_tokens:
                        raise RelayDenied(self._budget_text("token"))
                    estimate = projected_cost(emitted_tokens)
                    if estimate is not None and self.cost + estimate >= self.policy.max_cost_usd:
                        raise RelayDenied(self._budget_text("cost"))
                    if event.kind == "usage":
                        usage = event
                    data = wire(event)
                    if not data.get("timings"):
                        data.pop("timings", None)  # older runtime images don't know the field
                    yield data
            finally:
                estimated = usage is None
                # A request rejected before any output (e.g. HTTP 400) generated nothing and is
                # not billed, so it is not charged to the stage budget either.
                if usage is None and received:
                    # Interrupted streams may never deliver provider usage.
                    from providers.base import StreamEvent
                    usage = StreamEvent(kind="usage", input_tokens=request_tokens, output_tokens=emitted_tokens,
                                        total_tokens=request_tokens + emitted_tokens)
                if usage is not None:
                    cost, fallback = price_usd(self.model["provider"], self.model["model"],
                        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                        cached_tokens=usage.cached_tokens, reasoning_tokens=usage.reasoning_tokens)
                    if fallback:
                        self._warn_unpriced(self.model["provider"], self.model["model"])
                    charged = self._charged_tokens(usage)
                    self.tokens += charged
                    self.cost += float(cost or 0)
                    self.artifacts.append_cost({"stage": self.stage, "at": time.time(),
                        "provider": self.model["provider"], "model": self.model["model"],
                        "cost_usd": cost, "tokens": wire(usage), "charged_tokens": charged,
                        "estimated": estimated or fallback,
                        **({"fallback_price": True} if fallback else {})})
        finally:
            self.lock.release()

    def _warn_unpriced(self, provider, model):
        """Once per process and model: say the stage is being charged the fallback price."""
        key = (str(provider), str(model))
        if key in _UNPRICED_WARNED:
            return
        _UNPRICED_WARNED.add(key)
        text = (f"{provider}/{model or '(default)'} has no price in the pricing table; job budgets count it at a "
                f"conservative ${FALLBACK_INPUT_PER_MILLION:g}/${FALLBACK_OUTPUT_PER_MILLION:g} per million input/output "
                "tokens. Add the model under Settings → Model pricing for accurate costs.")
        import logging
        logging.getLogger("mucli").warning(text)
        callback = self.on_warning
        if callback is not None:
            try:
                callback({"stage": self.stage, "kind": "unpriced_model", "provider": provider, "model": model,
                          "message": text})
            except Exception:  # noqa: BLE001 - a warning must never break the stage
                pass

    def _context_window(self):
        if not hasattr(self, "_window"):
            try:
                window = self.provider.effective_context_window(self.model["model"])
                self._window = int(window) if window else None
            except Exception:  # noqa: BLE001
                self._window = None
        return self._window

    def _budget_text(self, kind, request_tokens=0):
        """Refusal text for the stage; records the reached cap (``limit_hit``) for the host."""
        self._record_limit(kind)
        if kind == "cost":
            earlier = (f" (plus ${self.prior_stage_cost:.2f} spent by earlier {self.stage} runs of this job)"
                       if self.prior_stage_cost else "")
            scope = (f"; the job cost limit is ${self.job_cost_limit:.2f}" if self.cost_scope == "job"
                     else " (budget: sandbox.max_cost_usd)")
            return (f"Stage cost budget exhausted: ${self.cost:.2f} of ${self.policy.max_cost_usd:.2f} left for the "
                    f"{self.stage} stage{earlier}{scope}. The job pauses here until a "
                    "human approves more or cancels it; stop and wait.")
        counted = "all tokens incl. cache reads" if self.policy.count_cache_reads else "new tokens, excluding cache reads"
        detail = f" (next request ~{request_tokens:,} tokens)" if request_tokens else ""
        earlier = (f" (plus {self.prior_stage_tokens:,} used by earlier {self.stage} runs of this job)"
                   if self.prior_stage_tokens else "")
        return (f"Stage token budget exhausted: {self.tokens:,} of {self.policy.max_tokens:,} tokens ({counted}) "
                f"used by the {self.stage} stage{earlier}{detail} (budget: sandbox.max_tokens). The job pauses here "
                "until a human approves more or cancels it; stop and wait.")

    def _record_limit(self, kind):
        if self.limit_hit is not None:
            return
        from ..limits import limit_detail
        if kind == "cost":
            if self.cost_scope == "job":
                self.limit_hit = limit_detail("cost", "job", self.stage, self.prior_job_cost + self.cost,
                                              self.job_cost_limit)
            else:
                self.limit_hit = limit_detail("cost", "stage", self.stage, self.prior_stage_cost + self.cost,
                                              self.stage_cost_limit)
        else:
            self.limit_hit = limit_detail("tokens", "stage", self.stage, self.prior_stage_tokens + self.tokens,
                                          self.stage_token_limit)

    def _charged_tokens(self, usage):
        total = int(usage.total_tokens or (usage.input_tokens or 0) + (usage.output_tokens or 0))
        if self.policy.count_cache_reads:
            return total
        cached = int(getattr(usage, "cache_read_tokens", 0) or getattr(usage, "cached_tokens", 0) or 0)
        return max(0, total - cached)

    def _report(self, exc):
        """Stage-facing error text plus a host-side diagnostic event. Secrets are redacted."""
        if isinstance(exc, RelayDenied):
            kind, text = "denied", str(exc)
        else:
            from mu.security.secret_paths import redact_secrets
            message = redact_secrets(str(exc) or "")[0].strip().splitlines()
            summary = (message[0] if message else "")[:300]
            kind, text = "provider", (f"Stage model request failed ({type(exc).__name__}"
                                      f"{': ' + summary if summary else ''}). See the job's Diagnostics for details.")
        callback = self.on_error
        if callback is not None:
            try:
                callback({"stage": self.stage, "kind": kind, "error_type": type(exc).__name__, "message": text,
                          "tokens_used": self.tokens, "max_tokens": self.policy.max_tokens,
                          "cost_used_usd": round(self.cost, 6), "max_cost_usd": self.policy.max_cost_usd,
                          "count_cache_reads": self.policy.count_cache_reads,
                          "provider": self.model.get("provider"), "model": self.model.get("model")})
            except Exception:  # noqa: BLE001 - diagnostics must never mask the original failure
                pass
        return text

    def app(self):
        from fastapi import FastAPI, Header, HTTPException, Request
        from fastapi.responses import StreamingResponse
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.post("/interaction")
        async def interaction(request: Request, x_mucli_stage_token: str = Header(default="")):
            try:
                self.authorize(x_mucli_stage_token)
            except RelayDenied as exc:
                raise HTTPException(403, str(exc)) from exc
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 1024 * 1024:
                    raise HTTPException(413, "interaction request is too large")
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError()
            except ValueError as exc:
                raise HTTPException(400, "invalid interaction request") from exc
            return {"response": self.interaction(payload)}

        @app.post("/environment")
        async def environment(request: Request, x_mucli_stage_token: str = Header(default="")):
            try:
                self.authorize(x_mucli_stage_token)
            except RelayDenied as exc:
                raise HTTPException(403, str(exc)) from exc
            if self.broker is None:
                raise HTTPException(404, "this stage has no other build environments")
            raw = await request.body()
            if len(raw) > 256 * 1024:
                raise HTTPException(413, "environment request is too large")
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError()
            except ValueError as exc:
                raise HTTPException(400, "invalid environment request") from exc

            def events():
                try:
                    for event in self.broker.run(str(payload.get("env") or ""), str(payload.get("command") or ""),
                                                 str(payload.get("cwd") or ""), payload.get("timeout_s")):
                        self.authorize(x_mucli_stage_token)
                        yield json.dumps(event) + "\n"
                except Exception as exc:  # noqa: BLE001 - the agent gets a result either way
                    yield json.dumps({"event": "result", "ok": False, "return_code": None, "error": str(exc)[:1000]}) + "\n"
            return StreamingResponse(events(), media_type="application/x-ndjson")

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
                except Exception as exc:  # noqa: BLE001 - every failure must reach the stage as an event
                    yield json.dumps({"kind": "error", "text": self._report(exc)}) + "\n"
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


class HarnessRelay(ModelRelay):
    """Relay for stages run by Claude Code / Codex: the CLI talks to its provider itself (through
    the egress bridge), so this relay only answers approvals (``/interaction``) and records usage
    (``/usage``) against the stage's cost and token budget."""

    PROVIDERS = {"claude": "anthropic", "codex": "openai"}

    def __init__(self, job, stage, model, policy, artifacts, *, agent, message_events=None, **kwargs):
        self.agent = agent
        self.message_events = message_events or (lambda: [])
        kwargs["provider_factory"] = lambda provider, name: object()
        super().__init__(job, stage, model, policy, artifacts, **kwargs)
        self.run_cost = 0.0  # this run's cost, replaced by the CLI's own total when it reports one
        self.run_estimated = True

    def stream(self, token, payload):
        raise RelayDenied(f"{self.stage} runs on {self.agent}; it has no relayed model")

    def record_usage(self, token, payload):
        self.authorize(token)
        with self.lock:
            usage = {k: max(0, int(payload.get(k) or 0)) for k in
                     ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens", "reasoning_tokens")}
            reported = payload.get("total_cost_usd")
            before = self.run_cost
            if reported is not None:  # the CLI's own total for this run (Claude Code) replaces estimates
                self.run_cost, self.run_estimated = max(0.0, float(reported)), False
            elif any(usage.values()):
                if not self.model.get("model") and str(payload.get("model") or ""):
                    self.model["model"] = str(payload["model"])[:120]  # the CLI's default model, as it reported it
                provider = self.PROVIDERS.get(self.agent, "")
                cost, fallback = price_usd(provider, self.model.get("model") or "",
                                           input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                                           cached_tokens=usage["cached_tokens"], reasoning_tokens=usage["reasoning_tokens"])
                if fallback:
                    self._warn_unpriced(provider, self.model.get("model") or "")
                self.run_cost += cost
            charged = 0
            if not payload.get("total_only"):
                charged = max(0, usage["input_tokens"] + usage["output_tokens"]
                              - (0 if self.policy.count_cache_reads else usage["cached_tokens"]))
                self.tokens += charged
            delta = self.run_cost - before
            self.cost += delta
            if delta or any(usage.values()):
                self.artifacts.append_cost({"stage": self.stage, "at": time.time(), "provider": self.agent,
                                            "model": self.model.get("model") or "", "cost_usd": round(delta, 6),
                                            "tokens": usage, "charged_tokens": charged,
                                            "estimated": self.run_estimated})
            if self.cost >= self.policy.max_cost_usd:
                return {"stop": True, "reason": self._budget_text("cost")}
            if self.tokens >= self.policy.max_tokens:
                return {"stop": True, "reason": self._budget_text("token")}
            return {"stop": False}

    def app(self):
        from fastapi import Header, HTTPException, Request
        app = super().app()

        @app.post("/messages")
        async def messages(x_mucli_stage_token: str = Header(default="")):
            """Human messages for this stage ("Message agent"), oldest first, with stable ids."""
            try:
                self.authorize(x_mucli_stage_token)
            except RelayDenied as exc:
                raise HTTPException(403, str(exc)) from exc
            return {"messages": [{"id": int(i), "text": str(t)[:20000]} for i, t in self.message_events()]}

        @app.post("/usage")
        async def usage(request: Request, x_mucli_stage_token: str = Header(default="")):
            raw = await request.body()
            if len(raw) > 64 * 1024:
                raise HTTPException(413, "usage report is too large")
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError()
                return self.record_usage(x_mucli_stage_token, payload)
            except RelayDenied as exc:
                raise HTTPException(403, str(exc)) from exc
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, "invalid usage report") from exc
        return app
