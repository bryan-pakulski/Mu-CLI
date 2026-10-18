"""Wash component gallery (spec §3.6).

``GET /design`` renders every base component in every state, Paper and Dusk
side by side, from static fixture data. It is a developer review surface (and
the input for ``scripts/design/snap.py``), so it is only served when
``MUCLI_DESIGN_GALLERY=1``; otherwise it is a plain 404.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

_ENABLED_VALUES = {"1", "true", "yes", "on"}


def gallery_enabled() -> bool:
    """True when the gallery is switched on for this process."""
    return str(os.environ.get("MUCLI_DESIGN_GALLERY", "")).strip().lower() in _ENABLED_VALUES


# Static fixtures: one entry per state the gallery shows. Kept here (not in
# the template) so later tracks can extend them without touching markup.
STATUS_SAMPLES = ("pending", "active", "waiting", "done", "failed", "skipped")
JOB_STATE_SAMPLES = ("selected", "provisioning", "plan_review", "implementing", "needs_input", "pushed", "failed", "cancelled")
TONES = ("accent", "running", "ok", "attention", "caution", "danger", "plan", "info", "neutral")
BUTTON_VARIANTS = ("primary", "secondary", "ghost", "tonal", "danger")
STEPPER = (
    ("done", "Preparing"),
    ("done", "Planning"),
    ("done", "Plan review"),
    ("active", "Implementing"),
    ("pending", "Checking"),
    ("pending", "Reviewing"),
    ("pending", "Pushing"),
)
TABLE_ROWS = (
    {"job": "Add rate limiting to /login", "repo": "atlas-api", "state": "plan_review", "cost": "$0.42", "time": "4m"},
    {"job": "Migrate settings to pydantic v2", "repo": "atlas-api", "state": "checking", "cost": "$1.08", "time": "12m"},
    {"job": "Fix flaky websocket test", "repo": "relay", "state": "pushed", "cost": "$0.19", "time": "1h"},
)


_SPRITE = Path(__file__).resolve().parents[1] / "static" / "icons" / "wash.svg"


@lru_cache(maxsize=1)
def sprite_icons() -> tuple:
    """Every `i-<name>` symbol in the generated icon sprite, sorted."""
    try:
        text = _SPRITE.read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(sorted(re.findall(r'<symbol id="i-([a-z0-9-]+)"', text)))


@router.get("/design", response_class=HTMLResponse, include_in_schema=False)
async def design_gallery(request: Request):
    if not gallery_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "design/gallery.html", {
        "nav_active": "design",
        "status_samples": STATUS_SAMPLES,
        "job_state_samples": JOB_STATE_SAMPLES,
        "tones": TONES,
        "button_variants": BUTTON_VARIANTS,
        "stepper": STEPPER,
        "table_rows": TABLE_ROWS,
        "themes": ("paper", "dusk"),
        "icons": sprite_icons(),
    })
