"""FastAPI routers for the GUI."""

# Keep the retrospective job analyzer under the existing /api/jobs prefix
# without making the main app factory aware of another lifecycle surface.
from . import jobs as _jobs  # noqa: F401
from . import job_analysis as _job_analysis  # noqa: F401

_jobs.router.include_router(_job_analysis.router)
