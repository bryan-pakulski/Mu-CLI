import socket
import threading
import time
from dataclasses import dataclass
from uuid import uuid4

from core.server import HeadlessUI, serve
from core.server_client import MuCLIServerClient
from core.session import Session, SessionManager
from mucli import handle_command
from providers.base import MessagePart, ProviderResponse


@dataclass
class DummyProvider:
    name: str = "dummy"
    model_name: str = "dummy-model"

    def get_available_models(self):
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(
            text="ok",
            parts=[MessagePart(type="text", text="ok")],
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )

    def upload_file(self, file_path, mime_type):
        return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _build_session():
    ui = HeadlessUI()
    session_manager = SessionManager(ui=ui, session_name=f"itest_{uuid4().hex}")
    session_manager.provider_config = {"provider": "dummy", "model": "dummy-model"}
    return Session(
        provider=DummyProvider(),
        thinking=False,
        system_instruction="integration-test",
        session_manager=session_manager,
        ui=ui,
        debug=False,
    )


def test_server_client_health_and_capabilities_round_trip():
    port = _free_port()
    session = _build_session()
    server_thread = threading.Thread(
        target=serve,
        args=(session, "127.0.0.1", port, handle_command),
        daemon=True,
    )
    server_thread.start()

    client = MuCLIServerClient(f"http://127.0.0.1:{port}", timeout=2.0)
    deadline = time.time() + 8.0
    while True:
        try:
            health = client.health()
            if health.get("ok"):
                break
        except Exception:  # noqa: BLE001
            if time.time() > deadline:
                raise
            time.sleep(0.1)

    capabilities = client.capabilities()

    assert health["status"] == "ok"
    assert capabilities["ok"] is True
    assert capabilities["capabilities"]["api_version"] == "v1"
    assert capabilities["capabilities"]["server_runtime"] == "authoritative"
