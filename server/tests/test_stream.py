from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.runtime.event_bus import event_bus


def test_websocket_stream_receives_published_event() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/stream/sessions/s-1") as websocket:
            import anyio

            anyio.run(event_bus.publish, "s-1", {"event_type": "log", "session_id": "s-1", "payload": {}})
            message = websocket.receive_json()
            assert message["event_type"] == "log"
            assert message["session_id"] == "s-1"
