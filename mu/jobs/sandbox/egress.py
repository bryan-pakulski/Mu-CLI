"""Optional registry proxy over a Unix socket, reusing Mucli's egress policy.

The agent still has --network none. Its localhost proxy forwards bytes to this
socket; the host proxy validates domains, ports and resolved public addresses.
"""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from mu.container.egress_proxy import EgressPolicy, _handle_client, _pipe


class EgressBridge:
    def __init__(self, directory, domains):
        self.path = Path(directory) / "egress.sock"
        self.policy = EgressPolicy(allow=tuple(domains))
        self.ready = threading.Event()
        self.error = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(10) or self.error:
            self.close()
            raise RuntimeError("registry proxy failed to start") from self.error
        return self

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        async def setup():
            self.server = await asyncio.start_unix_server(
                lambda r, w: _handle_client(r, w, self.policy), path=str(self.path), limit=65536)
            self.path.chmod(0o600)
        try:
            self.loop.run_until_complete(setup())
            self.ready.set()
            self.loop.run_forever()
        except Exception as exc:
            self.error = exc
            self.ready.set()
        finally:
            if hasattr(self, "server"):
                self.server.close()
                self.loop.run_until_complete(self.server.wait_closed())
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def close(self):
        if hasattr(self, "loop") and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if hasattr(self, "thread"):
            self.thread.join(timeout=3)


def start_container_proxy():
    if not Path("/relay/egress.sock").exists():
        return
    async def forward(reader, writer):
        remote_writer = None
        try:
            remote_reader, remote_writer = await asyncio.open_unix_connection("/relay/egress.sock")
            await asyncio.gather(_pipe(reader, remote_writer), _pipe(remote_reader, writer))
        finally:
            writer.close()
            if remote_writer:
                remote_writer.close()
    async def serve():
        server = await asyncio.start_server(forward, "127.0.0.1", 3128)
        async with server:
            await server.serve_forever()
    threading.Thread(target=lambda: asyncio.run(serve()), daemon=True).start()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[key] = "http://127.0.0.1:3128"
