"""Ollama routes (cloud / daemon→cloud / local): stable num_ctx, think mapping, side calls,
keep_alive, unique tool ids, telemetry and Retry-After (provider_latency_ollama_openai.md)."""
import io
import json
import urllib.error

import pytest

from providers.base import Message, MessagePart
from providers.ollama import LOCAL_MIN_NUM_CTX, SIDE_CALL_NUM_PREDICT, OllamaError, OllamaProvider


class FakeDaemon:
    """urlopen stand-in: /api/tags, /api/show and /api/chat; records chat payloads."""

    def __init__(self, *, tags=None, trained=262144, capabilities=("completion", "tools", "thinking"), chat=None):
        self.tags = tags if tags is not None else [{"name": "qwen3.8:27b"}, {"name": "glm-5.3-flash:cloud", "remote_host": "https://ollama.com"}]
        self.trained, self.capabilities = trained, list(capabilities)
        self.chat = chat or [{"message": {"content": "ok"}}, {"message": {"content": ""}, "done": True, "prompt_eval_count": 10,
                                                              "eval_count": 2, "load_duration": 1_500_000_000,
                                                              "prompt_eval_duration": 250_000_000, "eval_duration": 100_000_000}]
        self.payloads, self.paths = [], []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.paths.append(url.rsplit("/api/", 1)[-1])
        if url.endswith("/api/tags"):
            return io.BytesIO(json.dumps({"models": self.tags}).encode())
        if url.endswith("/api/show"):
            return io.BytesIO(json.dumps({"model_info": {"qwen35.context_length": self.trained},
                                          "capabilities": self.capabilities}).encode())
        self.payloads.append(json.loads(req.data))
        return io.BytesIO(b"".join(json.dumps(c).encode() + b"\n" for c in self.chat))


@pytest.fixture
def daemon(monkeypatch):
    fake = FakeDaemon()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    return fake


def provider(model="qwen3.8:27b", host="http://localhost:11434", variables=None, **kwargs):
    p = OllamaProvider(model, host=host, **kwargs)
    p.bind_session_variables(dict(variables or {}))
    p._preflight_done, p._cached_models = True, [model]
    return p


def ask(p, **kwargs):
    return list(p.stream([Message("user", [MessagePart("text", text="hi")])], **kwargs))


def test_routes_are_classified(daemon):
    assert provider("qwen3.8:27b").route() == "local"
    assert provider("glm-5.3-flash:cloud").route() == "daemon-cloud"
    assert provider("glm-5.3", host="https://ollama.com").route() == "cloud"
    tagged = FakeDaemon(tags=[{"name": "remote-model:latest", "remote_host": "https://ollama.com"}])
    import urllib.request
    old = urllib.request.urlopen
    urllib.request.urlopen = tagged
    try:
        assert provider("remote-model").route() == "daemon-cloud"  # remote_host in /api/tags
    finally:
        urllib.request.urlopen = old


def test_local_models_always_send_the_same_64k_plus_num_ctx(daemon):
    p = provider("qwen3.8:27b")
    for _ in range(3):
        ask(p)
    assert [pl["options"]["num_ctx"] for pl in daemon.payloads] == [LOCAL_MIN_NUM_CTX] * 3
    assert p.effective_context_window() == LOCAL_MIN_NUM_CTX
    # The default never goes below 64k; a bigger default is honoured up to the trained window.
    assert provider(variables={"ollama_num_ctx_default": 16384}).resolved_num_ctx() == LOCAL_MIN_NUM_CTX
    assert provider(variables={"ollama_num_ctx_default": 131072}).resolved_num_ctx() == 131072
    small = FakeDaemon(trained=32768)
    import urllib.request
    old = urllib.request.urlopen
    urllib.request.urlopen = small
    try:
        assert provider("tiny:7b").resolved_num_ctx() == 32768  # a small trained window caps it
    finally:
        urllib.request.urlopen = old
    assert provider(variables={"ollama_num_ctx": 40000}).resolved_num_ctx() == 40000  # explicit wins


def test_cloud_routes_never_send_num_ctx_or_keep_alive(daemon):
    for p in (provider("glm-5.3-flash:cloud", variables={"ollama_keep_alive": "30m"}),
              provider("glm-5.3", host="https://ollama.com", variables={"ollama_keep_alive": "30m"})):
        ask(p)
    for payload in daemon.payloads:
        assert "num_ctx" not in payload["options"] and "keep_alive" not in payload


def test_keep_alive_is_omitted_unless_set(daemon):
    ask(provider())
    ask(provider(variables={"ollama_keep_alive": "-1"}))
    assert "keep_alive" not in daemon.payloads[0] and daemon.payloads[1]["keep_alive"] == -1


def test_effort_maps_to_think_and_side_calls_never_think(daemon):
    ask(provider())
    ask(provider(), reasoning_effort="high")
    ask(provider(), reasoning_effort="low")
    provider().generate([Message("user", [MessagePart("text", text="summarize")])])
    no_effort, high, low, side = daemon.payloads
    assert "think" not in no_effort and high["think"] is True and low["think"] is False
    assert side["think"] is False and side["options"]["num_predict"] == SIDE_CALL_NUM_PREDICT
    ask(provider("gpt-oss:20b"), reasoning_effort="max")
    assert daemon.payloads[-1]["think"] == "high"


def test_think_is_not_sent_to_models_without_thinking(monkeypatch):
    fake = FakeDaemon(capabilities=("completion", "tools"))
    monkeypatch.setattr("urllib.request.urlopen", fake)
    ask(provider("llama3:8b"), reasoning_effort="high")
    provider("llama3:8b").generate([Message("user", [MessagePart("text", text="s")])])
    assert all("think" not in p for p in fake.payloads)


def test_side_model_takes_summaries_off_the_main_model(daemon):
    p = provider(variables={"ollama_side_model": "qwen3:4b"})
    p.generate([Message("user", [MessagePart("text", text="summarize")])])
    ask(p)
    assert daemon.payloads[0]["model"] == "qwen3:4b" and daemon.payloads[1]["model"] == "qwen3.8:27b"


def test_tool_call_ids_are_unique_across_responses(monkeypatch):
    chat = [{"message": {"content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": {"filename": "a"}}}]}},
            {"message": {"content": ""}, "done": True}]
    monkeypatch.setattr("urllib.request.urlopen", FakeDaemon(chat=chat))
    ids = [e.tool_call_id for _ in range(2) for e in ask(provider()) if e.kind == "tool_call_start"]
    assert len(ids) == 2 and ids[0] != ids[1] and all(i.startswith("ollama_") for i in ids)


def test_timings_reach_the_response(daemon):
    response = provider().drain_stream(provider().stream([Message("user", [MessagePart("text", text="hi")])]))
    assert response.timings == {"load_s": 1.5, "prompt_eval_s": 0.25, "eval_s": 0.1}


def test_rate_limits_carry_retry_after(monkeypatch):
    def limited(req, timeout=None):
        if req.full_url.endswith(("/api/tags", "/api/show")):
            return io.BytesIO(b'{"models": []}')
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "7"}, io.BytesIO(b"slow down"))
    monkeypatch.setattr("urllib.request.urlopen", limited)
    with pytest.raises(OllamaError) as info:
        ask(provider("glm-5.3", host="https://ollama.com"))
    assert info.value.retry_after == 7.0
    from mu.agent.retry import is_transient_provider_error
    assert is_transient_provider_error(info.value)


def test_local_first_token_timeout_is_not_a_blind_retry(monkeypatch):
    import socket

    def slow(req, timeout=None):
        if req.full_url.endswith(("/api/tags", "/api/show")):
            return io.BytesIO(b'{"models": []}')
        raise socket.timeout("timed out")
    monkeypatch.setattr("urllib.request.urlopen", slow)
    p = provider()
    assert p.request_timeout_for() == 900.0
    with pytest.raises(OllamaError) as info:
        ask(p)
    from mu.agent.retry import is_transient_provider_error
    assert "no output within 900s" in str(info.value) and not is_transient_provider_error(info.value)
    assert provider("glm-5.3", host="https://ollama.com").request_timeout_for() == 300.0
