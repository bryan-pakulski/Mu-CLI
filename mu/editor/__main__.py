"""Private stdio JSON-lines transport: python -m mu.editor --workspace PATH.

Each lane has one active and one pending request. Superseded work is cancelled,
not queued indefinitely. Provider I/O can finish its current blocking read before
cancellation takes effect; the client independently rejects stale responses.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading

from .context import scrub
from .service import EditorService


class Lane:
    def __init__(self, service, send):
        self.service, self.send = service, send
        self.condition = threading.Condition()
        self.pending = None
        self.active = None
        self.closed = False
        threading.Thread(target=self.work, daemon=True).start()

    def submit(self, request):
        with self.condition:
            if self.active:
                self.active[1].set()
            if self.pending:
                self.send({"id": self.pending["id"], "error": "Request superseded"})
            self.pending = request
            self.condition.notify()

    def cancel(self, request_id):
        with self.condition:
            if self.active and self.active[0] == request_id:
                self.active[1].set()
            if self.pending and self.pending["id"] == request_id:
                self.pending = None

    def close(self):
        with self.condition:
            self.closed = True
            if self.active:
                self.active[1].set()
            self.pending = None
            self.condition.notify()

    def work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending is not None or self.closed)
                if self.closed:
                    return
                request, self.pending = self.pending, None
                cancel = threading.Event()
                self.active = request["id"], cancel
            try:

                def emit(event):
                    if not cancel.is_set():
                        self.send(dict(event, id=request["id"]))

                result = self.service.run(
                    request["method"], request.get("params", {}), cancel, emit
                )
                if not cancel.is_set():
                    self.send({"id": request["id"], "result": result})
            except Exception as exc:
                if not cancel.is_set():
                    self.send({"id": request["id"], "error": scrub(str(exc))[:1000]})
            finally:
                with self.condition:
                    self.active = None


def serve(root, source, target, service_factory=EditorService):
    lock = threading.Lock()

    def send(message):
        with lock:
            target.write(json.dumps(message, ensure_ascii=False) + "\n")
            target.flush()

    service = service_factory(root)
    service.start()
    lane = Lane(service, send)
    try:
        while True:
            line = source.readline(3_000_001)
            if not line:
                break
            request_id = None
            try:
                if len(line) > 3_000_000:
                    raise ValueError("Editor request exceeds size limit")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Expected a JSON object")
                request_id = request.get("id")
                method = request.get("method")
                if method == "cancel":
                    lane.cancel(request.get("params", {}).get("id"))
                elif method == "sync":
                    service.sync(request.get("params", {}))
                elif method == "ping":
                    send(
                        {
                            "id": request_id,
                            "result": {
                                "protocol": 1,
                                "workspace": str(service.workspace.root),
                            },
                        }
                    )
                elif method in {
                    "analyze",
                    "edit",
                    "search",
                    "context",
                    "reset",
                }:
                    if not isinstance(request_id, int) or not isinstance(
                        request.get("params"), dict
                    ):
                        raise ValueError(
                            "Request requires an integer id and object params"
                        )
                    if method in {"analyze", "edit", "context"}:
                        # Freeze versions in wire order. A subsequent keystroke
                        # may sync a newer version before the worker starts.
                        request["params"] = service.prepare(request["params"])
                    lane.submit(request)
                else:
                    raise ValueError("Unknown editor method")
            except (ValueError, TypeError, AttributeError) as exc:
                send({"id": request_id, "error": scrub(str(exc))})
    finally:
        lane.close()
        service.close()


def main():
    parser = argparse.ArgumentParser(description="Mucli Neovim stdio service")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    # SDKs/libraries may print diagnostics; stdout belongs exclusively to the protocol.
    protocol_out = sys.stdout
    sys.stdout = sys.stderr
    serve(args.workspace, sys.stdin, protocol_out)


if __name__ == "__main__":
    main()
