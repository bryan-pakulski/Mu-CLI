#!/usr/bin/env python3
"""Lightweight HTTP proxy+static server for Mu-CLI GUI.

Serves static files from gui/ directory AND proxies /api/* requests
to the MuCLI API server, eliminating CORS issues by serving everything
from a single origin.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

GUI_DIR = Path(__file__).resolve().parent

# MIME types for static files
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".webp": "image/webp",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".pdf": "application/pdf",
    ".map": "application/json",
}


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Serves static GUI files and proxies /api/* to the MuCLI server."""

    server_version = "MuCLIGUI/0.1"

    # Class-level config set by the launcher
    api_host = "127.0.0.1"
    api_port = 8765

    def log_message(self, fmt, *args):
        print(f"[proxy] {fmt % args}", flush=True)

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _safe_write_response(self, status, body, headers_dict=None):
        """Write a complete HTTP response, silently catching BrokenPipeError."""
        try:
            self.send_response(status)
            ct = "application/json"
            if headers_dict:
                for k, v in headers_dict.items():
                    self.send_header(k, v)
                    if k.lower() == "content-type":
                        ct = v
            # Always ensure Content-Type and Content-Length are set
            if not headers_dict or "Content-Type" not in (headers_dict or {}):
                self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _proxy_request(self, method="GET"):
        """Forward the request to the MuCLI API server using raw HTTP for streaming support."""
        api_url = f"http://{self.api_host}:{self.api_port}{self.path}"
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        headers = {}
        for key in ("Content-Type", "Authorization"):
            val = self.headers.get(key)
            if val:
                headers[key] = val

        req = urllib.request.Request(api_url, data=body, headers=headers, method=method)

        try:
            resp = urllib.request.urlopen(req, timeout=60)
            # Check if this is an SSE stream — don't buffer, stream directly
            content_type = resp.headers.get("Content-Type", "")
            is_sse = "text/event-stream" in content_type

            if is_sse:
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self._send_cors_headers()
                    self.end_headers()
                    while True:
                        chunk = resp.read(4096)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
                return

            # Non-streaming response: read fully and forward
            resp_body = resp.read()
            fwd_headers = {}
            for key, val in resp.getheaders():
                if key.lower() not in ("transfer-encoding", "connection", "content-length", "server"):
                    fwd_headers[key] = val
            fwd_headers["Content-Length"] = str(len(resp_body))
            self._safe_write_response(resp.status, resp_body, fwd_headers)

        except urllib.error.HTTPError as e:
            # The upstream returned an error (4xx/5xx). Forward it faithfully.
            try:
                error_body = e.read()
            except Exception:
                error_body = b""
            self._safe_write_response(e.code, error_body)

        except urllib.error.URLError as e:
            # Can't reach the upstream at all (connection refused, etc.)
            err_body = json.dumps({"ok": False, "error": f"Proxy error: {e.reason}"}).encode()
            self._safe_write_response(502, err_body)

        except Exception as e:
            err_body = json.dumps({"ok": False, "error": f"Proxy error: {e}"}).encode()
            self._safe_write_response(502, err_body)

    def do_GET(self):
        parsed = urlparse(self.path)

        # Proxy all /api/* requests to the MuCLI server
        if parsed.path.startswith("/api/"):
            self._proxy_request("GET")
            return

        # Serve static files
        file_path = GUI_DIR / parsed.path.lstrip("/")
        if file_path.is_dir():
            file_path = file_path / "index.html"

        if file_path.is_file():
            ext = file_path.suffix.lower()
            mime = MIME_TYPES.get(ext, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-cache")
            self._send_cors_headers()
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            # SPA fallback: serve index.html for unknown routes
            index = GUI_DIR / "index.html"
            if index.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self._send_cors_headers()
                self.end_headers()
                with open(index, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(b"Not found")

    def do_POST(self):
        self._proxy_request("POST")

    def do_PUT(self):
        self._proxy_request("PUT")

    def do_DELETE(self):
        self._proxy_request("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()


def run_server(host="127.0.0.1", port=4173, api_host="127.0.0.1", api_port=8765):
    """Run the proxy+static server."""
    ProxyHandler.api_host = api_host
    ProxyHandler.api_port = api_port
    server = ThreadingHTTPServer((host, port), ProxyHandler)
    server.serve_forever()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Mu-CLI GUI proxy server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4173)
    p.add_argument("--api-host", default="127.0.0.1")
    p.add_argument("--api-port", type=int, default=8765)
    args = p.parse_args()
    run_server(args.host, args.port, args.api_host, args.api_port)