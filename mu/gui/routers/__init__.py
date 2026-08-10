"""FastAPI routers for the GUI."""

# Keep the retrospective job analyzer under the existing /api/jobs prefix
# without making the main app factory aware of another lifecycle surface.
from . import jobs as _jobs  # noqa: F401
from . import job_analysis as _job_analysis  # noqa: F401

_jobs.router.include_router(_job_analysis.router)

# Saved-session history is a durable-data read, not an in-memory-session read.
# Replace the legacy /current/history route with the authoritative wrapper while
# retaining the existing history formatter for timeline semantics.
from . import sessions as _sessions  # noqa: E402,F401
from . import session_history as _session_history  # noqa: E402,F401

_sessions.router.routes[:] = [
    route
    for route in _sessions.router.routes
    if not (
        getattr(route, "path", "") == "/current/history"
        and "GET" in (getattr(route, "methods", set()) or set())
    )
]
_sessions.router.include_router(_session_history.router)
