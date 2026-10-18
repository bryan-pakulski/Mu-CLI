"""The job model relay serves a parent and its sub-agents at once.

rsq-cloud job c33252f5: the implement agent spawned a sub-agent whose model request arrived while
the parent's was still streaming; the relay refused it ("only one model request per stage may run
at a time") and the sub-agent's turn died. Requests now share bounded slots: extra requests wait,
budgets account for every request in flight through reservations, and a budget refusal still pauses.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from providers.base import Message, MessagePart, StreamEvent
from mu.jobs.core.artifacts import JobArtifacts
from mu.jobs.sandbox import model_relay
from mu.jobs.sandbox.model_relay import ModelRelay, RelayDenied, relay_concurrency, wire
from mu.jobs.sandbox.policy import SandboxPolicy


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


class GatedProvider:
    """Streams one delta, then blocks until released (a long parent request)."""
    model_name = ""

    def __init__(self):
        self.started = threading.Semaphore(0)
        self.release = threading.Event()
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def stream(self, messages, **kwargs):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            self.started.release()
            yield StreamEvent(kind="text_delta", text="ok")
            self.release.wait(5)
            yield StreamEvent(kind="usage", input_tokens=1_000, output_tokens=50, total_tokens=1_050)
            yield StreamEvent(kind="done")
        finally:
            with self.lock:
                self.active -= 1


def make_relay(tmp_path, provider, *, job=None, policy=None):
    art = JobArtifacts("j", str(tmp_path / "art")).ensure()
    job = job or SimpleNamespace(id="j", max_cost_usd=None)
    return ModelRelay(job, "implement", {"provider": "anthropic", "model": "claude-sonnet-5-5"},
                      policy or SandboxPolicy(), art, provider_factory=lambda p, m: provider)


def request(relay, out, key):
    try:
        out[key] = list(relay.stream(relay.token, {"messages": wire([Message("user", [MessagePart("text", text=key)])])}))
    except RelayDenied as exc:
        out[key] = exc


def test_parent_and_subagent_requests_run_together(tmp_path):
    provider = GatedProvider()
    relay = make_relay(tmp_path, provider)
    out = {}
    parent = threading.Thread(target=request, args=(relay, out, "parent"))
    parent.start()
    assert provider.started.acquire(timeout=5)
    child = threading.Thread(target=request, args=(relay, out, "subagent"))
    child.start()
    assert provider.started.acquire(timeout=5), "the sub-agent request must not wait for the parent"
    provider.release.set()
    parent.join(5)
    child.join(5)
    assert not isinstance(out["parent"], Exception) and not isinstance(out["subagent"], Exception)
    assert provider.peak == 2
    assert relay.reserved_cost == 0 and relay.reserved_tokens == 0
    assert relay.tokens > 0  # both requests were charged


def test_requests_beyond_the_slots_wait_instead_of_failing(tmp_path, monkeypatch):
    provider = GatedProvider()
    relay = make_relay(tmp_path, provider, job=SimpleNamespace(id="j", max_cost_usd=None, max_subagents=0))
    assert relay.concurrency == 1  # sub-agents off: one request at a time
    out = {}
    first = threading.Thread(target=request, args=(relay, out, "first"))
    first.start()
    assert provider.started.acquire(timeout=5)
    second = threading.Thread(target=request, args=(relay, out, "second"))
    second.start()
    second.join(0.3)
    assert second.is_alive(), "the second request queues for the slot"
    provider.release.set()
    first.join(5)
    second.join(5)
    assert not isinstance(out["first"], Exception) and not isinstance(out["second"], Exception)
    assert provider.peak == 1


def test_a_request_that_cannot_get_a_slot_in_time_is_refused_with_a_clear_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(model_relay, "SLOT_WAIT_S", 0.2)
    provider = GatedProvider()
    relay = make_relay(tmp_path, provider, job=SimpleNamespace(id="j", max_cost_usd=None, max_subagents=0))
    out = {}
    first = threading.Thread(target=request, args=(relay, out, "first"))
    first.start()
    assert provider.started.acquire(timeout=5)
    request(relay, out, "late")
    provider.release.set()
    first.join(5)
    assert isinstance(out["late"], RelayDenied) and "model relay busy" in str(out["late"])


def test_concurrent_requests_cannot_jointly_overrun_the_cost_budget(tmp_path):
    import json
    from utils.token_estimator import estimate_tokens
    provider = GatedProvider()
    big = "x " * 40_000
    payload = {"messages": wire([Message("user", [MessagePart("text", text=big)])])}
    one = model_relay.price_usd("anthropic", "claude-sonnet-5-5",
                                input_tokens=estimate_tokens(json.dumps(payload)), output_tokens=0)[0]
    assert one, "the model must be priced for this test"
    # Room for one request's projected input cost but not two at once.
    relay = make_relay(tmp_path, provider, policy=SandboxPolicy(max_cost_usd=one * 1.5))
    out = {}
    first = threading.Thread(target=lambda: out.setdefault("first", list(relay.stream(
        relay.token, {"messages": wire([Message("user", [MessagePart("text", text=big)])])}))))
    first.start()
    assert provider.started.acquire(timeout=5)
    with pytest.raises(RelayDenied, match="cost budget"):
        list(relay.stream(relay.token, {"messages": wire([Message("user", [MessagePart("text", text=big)])])}))
    provider.release.set()
    first.join(5)
    assert relay.reserved_cost == 0


@pytest.mark.parametrize("subagents,expected", [(None, 4), (0, 1), (2, 3), (50, 8)])
def test_relay_concurrency_follows_the_job_sub_agent_limit(subagents, expected):
    assert relay_concurrency(SimpleNamespace(max_subagents=subagents)) == expected
