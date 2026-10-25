"""Shared fixtures for research-mode tests: a loopback page server and a session.

Pages are served by a stdlib ``http.server`` on 127.0.0.1, so reading tests
never touch the internet.
"""

from __future__ import annotations

import http.server
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Dict, Iterator, Tuple

from mu.research import ResearchState
from mu.session.tool_cache import ToolResultCache


@contextmanager
def serve_pages(pages: Dict[str, Tuple[str, bytes]]) -> Iterator[str]:
    """Serve ``{path: (content_type, body)}``; yields the base URL. Unknown paths 404."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            page = pages.get(self.path.split("?")[0])
            if page is None:
                self.send_response(404)
                self.end_headers()
                return
            ctype, body = page
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def research_session(folder_context=None, variables=None):
    """A minimal agent session: its own ResearchState and result cache."""
    manager = SimpleNamespace(research=ResearchState(), current_session_name="")
    return SimpleNamespace(
        session_manager=manager,
        tool_result_cache=ToolResultCache(),
        folder_context=folder_context,
        variables=dict(variables or {"agent_mode": "research"}),
    )


def tool_context(session):
    from mu.tools.descriptors import ToolExecutionContext

    return ToolExecutionContext(
        folder_context=session.folder_context, variables=session.variables, session=session
    )


def article(title: str, paragraphs, *, extra_head: str = "", heading: str = "") -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    h2 = f"<h2>{heading}</h2>" if heading else ""
    return (
        "<html><head><title>" + title + "</title>" + extra_head + "</head><body>"
        "<header><nav><a href='/'>Home</a> <a href='/about'>About</a> <a href='/blog'>Blog</a></nav></header>"
        "<div class='cookie-banner'>We use cookies to improve your experience. Accept all cookies?</div>"
        "<article><h1>" + title + "</h1>" + h2 + body + "</article>"
        "<footer>Copyright 2026 Example Corp. All rights reserved. Privacy policy.</footer>"
        "</body></html>"
    ).encode("utf-8")
