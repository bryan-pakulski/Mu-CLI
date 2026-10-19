"""The persistent shell (documentation/design/mu_style_guide.md §3, revision 2).

A browser opening any destination URL gets one shell document: the rail, the
mode's watercolour and a flat leaf. Each destination page loads into the leaf as
a keep-alive view (an iframe of the same URL in *embed* mode) on first visit and
stays alive, so switching between destinations never reloads a page, and live
streams, scroll positions and unsaved input survive. ``static/js/mu_shell.js``
runs the shell; ``wash_shell.js`` runs the embed side (URL and title reports,
cross-destination links).

Embed mode is decided per request: ``?embed=1`` or ``Sec-Fetch-Dest: iframe``.
Requests that don't ask for HTML (API clients, tests) and ``?standalone=1`` get
the standalone page, rail included, exactly as before.
"""

from __future__ import annotations

from typing import Dict, Tuple

# Destination paths the shell hosts, each with its rail item and mode (accent + wash).
DESTINATIONS: Dict[str, Tuple[str, str]] = {
    "/": ("chat", "chat"),
    "/work": ("work", "work"),
    "/orchestration": ("orchestration", "orchestration"),
    "/containers": ("environments", "environments"),
    "/trace": ("insights", "insights"),
    "/jobs/trace": ("insights", "insights"),
    "/models/costs": ("insights", "insights"),
    "/settings": ("settings", "settings"),
}


def is_embed(request) -> bool:
    """The page renders inside the shell's leaf: no rail, no ambient wash."""
    if request.query_params.get("embed") == "1":
        return True
    return request.headers.get("sec-fetch-dest", "").lower() in ("iframe", "frame")


def wants_shell(request) -> bool:
    """A browser navigating to a destination (not an API client, not a frame)."""
    if request.method != "GET" or request.url.path not in DESTINATIONS:
        return False
    if request.query_params.get("standalone") == "1" or is_embed(request):
        return False
    dest = request.headers.get("sec-fetch-dest", "").lower()
    if dest and dest != "document":
        return False
    return "text/html" in request.headers.get("accept", "")


def install(app, templates) -> None:
    """Serve the shell for browser navigations to destination paths."""

    async def persistent_shell(request, call_next):
        if wants_shell(request):
            nav, mode = DESTINATIONS[request.url.path]
            return templates.TemplateResponse(request, "shell.html", {
                "nav_active": nav, "mode": mode, "destinations": DESTINATIONS,
            })
        return await call_next(request)

    app.middleware("http")(persistent_shell)
    templates.env.globals["mu_is_embed"] = is_embed
    templates.env.globals["mu_destinations"] = DESTINATIONS
