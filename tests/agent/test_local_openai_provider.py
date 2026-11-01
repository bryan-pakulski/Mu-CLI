"""Local discovery, wire compatibility, token budgets and credential isolation."""
import json
from types import SimpleNamespace

import httpx
from openai import OpenAI
import pytest

from providers.base import CacheHint, Message, MessagePart, ToolDefinition
from providers.local import LocalOpenAIProvider, normalize_base_url


@pytest.fixture
def endpoint(monkeypatch):
    state = SimpleNamespace(requests=[], models=[{"id": "glm-5.3-flash", "object": "model", "created": 0,
                            "owned_by": "local", "context_window": 102400, "max_output_tokens": 8192,
                            "capabilities": {"reasoning_effort": True}}], failure=False)
    def serve(request):
        state.requests.append(request)
        if state.failure:
            return httpx.Response(503, json={"error": {"message": "private credential, never display"}})
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={"object": "list", "data": state.models})
        payload = json.loads(request.content)
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "hello", "reasoning_content": "thinking"}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"value":7}'}}]}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 8, "total_tokens": 38}},
        ]
        body = ''.join('data: '+json.dumps({"id": "r", "object": "chat.completion.chunk", "created": 0, "model": payload['model'], **c})+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={'content-type':'text/event-stream'})
    def client(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(serve)))
    monkeypatch.setattr('providers.local.OpenAI', client)
    monkeypatch.setenv('OPENAI_API_KEY', 'cloud-secret')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://cloud.example/v1')
    monkeypatch.delenv('LOCAL_LLM_API_KEY', raising=False)
    monkeypatch.delenv('LOCAL_LLM_BASE_URL', raising=False)
    return state


def test_discovery_refresh_context_and_cloud_isolation(endpoint):
    provider = LocalOpenAIProvider('glm-5.3-flash')
    assert provider.get_available_models() == ['glm-5.3-flash']
    assert provider.effective_context_window() == 102400
    assert provider.effective_response_reserve() == 2048
    assert provider.compaction_safety_factor() == 1.3
    assert str(endpoint.requests[0].url) == 'http://127.0.0.1:8080/v1/models'
    assert endpoint.requests[0].headers['authorization'] == 'Bearer local'
    endpoint.models = [{"id": "new-model", "object": "model", "created": 0, "owned_by": "local"}]
    assert provider.get_available_models() == ['new-model']
    provider.model_name = 'new-model'
    assert provider.effective_context_window() == 4096
    provider.apply_session_variables({'local_context_window': 12000, 'local_base_url': 'http://server:1234'})
    assert provider.effective_context_window() == 12000
    assert endpoint.requests[-1].url.host == 'server'


def test_stream_tools_usage_and_local_wire_fields(endpoint):
    p = LocalOpenAIProvider('glm-5.3-flash', api_key='private-local-key')
    p.apply_session_variables({'openai_api':'responses', 'openai_prompt_cache_retention':'24h'})
    events = list(p.stream([Message('user', [MessagePart('text', text='hello')])],
                          tools=[ToolDefinition(name='lookup', description='lookup', parameters={'type':'object'})],
                          reasoning_effort='high'))
    request = endpoint.requests[-1]
    body = json.loads(request.content)
    assert request.url.path == '/v1/chat/completions'
    assert body['reasoning_effort'] == 'high' and body['max_tokens'] == 2048
    assert 'prompt_cache_retention' not in body
    assert request.headers['authorization'] == 'Bearer private-local-key'
    assert any(e.kind == 'text_delta' and e.text == 'hello' for e in events)
    assert any(e.kind == 'usage' and e.input_tokens == 30 for e in events)
    assert any(e.kind == 'tool_call_complete' and e.tool_args == {'value': 7} for e in events)
    assert not p.supports_input_modality('image')


def test_failed_discovery_has_no_fallback_or_secret_error(endpoint):
    endpoint.failure = True
    p = LocalOpenAIProvider('x')
    assert p.get_available_models() == []
    assert '503' in p.discovery_error
    assert 'credential' not in p.discovery_error


@pytest.mark.parametrize('url', ['file:///tmp/model','http://user:pass@host/v1','http://host/v1?key=x','http://host/#x','http://host:bad'])
def test_unsafe_endpoint_shapes_rejected(url):
    with pytest.raises(ValueError): normalize_base_url(url)


def test_local_factory_picker_and_pricing(endpoint, monkeypatch):
    import mucli
    from utils.model_pricing import estimate_model_cost
    p = mucli.select_provider_and_model('local', 'glm-5.3-flash', allow_prompt=False, local_base_url='http://localhost:8080/v1')
    assert p.name == 'local' and p.model_name == 'glm-5.3-flash'
    assert estimate_model_cost(provider='local', model_name='gpt-6', input_tokens=100, output_tokens=20)['api_cost_usd'] == 0


def test_child_connection_changes_do_not_close_parent_and_reset_clears_key(endpoint):
    p = LocalOpenAIProvider('glm-5.3-flash', base_url='http://parent:8080/v1', api_key='parent-key')
    p.get_available_models()
    child = p.clone_for_child()
    child.apply_session_variables({'local_base_url':'http://child:9000/v1', 'local_api_key':'child-key'})
    assert p.get_available_models() == ['glm-5.3-flash']
    assert endpoint.requests[-1].url.host == 'parent'
    assert endpoint.requests[-1].headers['authorization'] == 'Bearer parent-key'
    child.apply_session_variables({'local_base_url':'', 'local_api_key':''})
    child.get_available_models()
    assert endpoint.requests[-1].url.host == '127.0.0.1'
    assert endpoint.requests[-1].headers['authorization'] == 'Bearer local'
