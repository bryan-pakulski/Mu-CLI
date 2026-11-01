"""Discovery and saved endpoint switching through the actual GUI routes."""
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers.providers import router
from mu.gui.routers.inspector import _build_variable_entry
from providers.local import LocalOpenAIProvider


def test_discover_switch_restore_and_secret_masking(monkeypatch):
    seen = []
    def discover(self):
        seen.append((self.BASE_URL, self._client.api_key))
        self.discovery_error = ''
        return ['glm-5.3-flash', 'second-model']
    monkeypatch.setattr(LocalOpenAIProvider, 'get_available_models', discover)
    entry = _build_variable_entry('local_api_key', 'saved-secret')
    assert entry['secret'] and entry['is_set'] and entry['value'] is None
    variables = {'local_base_url': 'http://old:8080/v1', 'local_api_key': 'saved-secret'}
    manager = SimpleNamespace(provider_config={'provider':'local','model':'glm-5.3-flash'}, save_history=Mock())
    session = SimpleNamespace(variables=variables, session_manager=manager, provider=None)
    app = FastAPI()
    app.state.session_by_name = lambda: session
    app.include_router(router, prefix='/api/providers')
    with TestClient(app) as client:
        assert 'local' in [p['name'] for p in client.get('/api/providers').json()['providers']]
        response = client.post('/api/providers/local/discover', json={'local_base_url':'http://new:1234', 'local_api_key':'new-secret'})
        assert response.status_code == 200 and response.json()['models'] == ['glm-5.3-flash', 'second-model']
        assert seen[-1] == ('http://new:1234/v1', 'new-secret')
        assert 'secret' not in response.text
        assert variables['local_base_url'] == 'http://old:8080/v1'  # discovery does not mutate the active session
        response = client.post('/api/providers/switch', json={'provider':'local', 'model':'second-model', 'local_base_url':'http://new:1234','local_api_key':'new-secret'})
        assert response.status_code == 200
        assert variables['local_base_url'] == 'http://new:1234/v1'
        assert session.provider.name == 'local' and session.provider.model_name == 'second-model'
        assert manager.save_history.called
        current = client.get('/api/providers/current')
        assert current.json()['local_api_key_set'] is True and 'new-secret' not in current.text
        client.get('/api/providers/local/models')
        assert seen[-1] == ('http://new:1234/v1', 'new-secret')
        bad = client.post('/api/providers/switch', json={'provider':'local','model':'x','local_base_url':'file:///tmp/x'})
        assert bad.status_code == 400 and session.provider.model_name == 'second-model'
    from mucli import init_provider, local_provider_settings, sync_provider_settings
    restored = init_provider('local', 'second-model', **local_provider_settings(variables))
    session.provider = restored
    sync_provider_settings(session)
    assert restored.BASE_URL == 'http://new:1234/v1' and restored._client.api_key == 'new-secret'


def test_new_session_connection_inherits_masked_local_key_only():
    from mu.gui.routers.sessions import _provider_seed_vars
    from fastapi import HTTPException
    import pytest
    values = _provider_seed_vars({'provider':'local','local_base_url':'http://localhost:8080'},
                                 {'local_api_key':'saved-secret','ollama_api_key':'cloud-key'})
    assert values == {'local_base_url':'http://localhost:8080/v1','local_api_key':'saved-secret'}
    with pytest.raises(HTTPException):
        _provider_seed_vars({'provider':'local','local_base_url':'file:///bad'})
