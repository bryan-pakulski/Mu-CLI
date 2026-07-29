from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mobile_busy_state_uses_authoritative_server_busy_flag():
    source = read("mobile/android/src/hooks/useChatSession.ts")
    assert "response.is_busy || response.external_active" not in source
    assert "const busy = Boolean(response.active && response.is_busy)" in source
    assert "externalWriteAtRef" in source
    assert "setActivityLabel('Finishing')" in source


def test_mobile_handles_terminal_artifact_and_history_events():
    source = read("mobile/android/src/hooks/useChatSession.ts")
    assert "kind === 'artifact_created'" in source
    assert "kind === 'history_refresh'" in source
    assert "artifactRevision" in source


def test_artifacts_are_visible_above_the_mobile_composer():
    source = read("mobile/android/src/screens/ChatScreen.tsx")
    artifact = source.index('<ArtifactStrip sessionName={activeSessionName} refreshKey={artifactRevision} />')
    composer = source.index('<Composer', artifact)
    assert artifact < composer


def test_artifact_list_bypasses_mobile_http_cache():
    api = read("mobile/android/src/api/artifacts.ts")
    router = read("mu/gui/routers/artifacts.py")
    assert "{ query: { _ts: Date.now() } }" in api
    assert 'Cache-Control"] = "no-store, max-age=0"' in router


def test_host_replays_worker_artifacts_after_container_turn():
    source = read("mu/gui/routers/chat.py")
    assert "_replay_new_artifacts" in source
    assert '"kind": "artifact_created"' in source
    assert '"artifacts": new_artifacts' in source


def test_external_watcher_activity_expires_and_is_session_scoped():
    watcher = read("mu/gui/watcher.py")
    sessions = read("mu/gui/routers/sessions.py")
    assert "_EXTERNAL_ACTIVITY_TTL_SECONDS" in watcher
    assert "watcher.external_active_for(sm.current_session_name)" in sessions
