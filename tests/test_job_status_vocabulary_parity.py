"""The job status vocabulary must read the same on every surface.

The terminal (``mu/ui/jobs/theme.py``), web (``work/view.js``) and Android
(``theme/status.ts``, ``api/jobNotifications.ts``) used to each carry a copy
of the state → status → label tables, and the copies had drifted: the
terminal labelled ``provisioning`` as "provisioning" while web and mobile
said "preparing". Every surface now reads the shared vocabulary
(``mu/design/vocabulary.json``, Wash spec §3.1): the terminal through
``mu.design``, the web through the generated ``window.WASH``
(``static/js/wash_tokens.js``) and Android through ``wash.generated.ts``.
This test keeps that true: complete tables, no local copies.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mu.design import load_vocabulary
from mu.jobs.core.states import JobState
from mu.ui.jobs import theme

ROOT = Path(__file__).resolve().parents[1]
WEB_VIEW = ROOT / "mu/gui/static/js/work/view.js"
WEB_TOKENS = ROOT / "mu/gui/static/js/wash_tokens.js"
WEB_LAYOUT = ROOT / "mu/gui/templates/_layout.html"
WEB_WORK = ROOT / "mu/gui/templates/work.html"
MOBILE_STATUS = ROOT / "mobile/android/src/theme/status.ts"
MOBILE_NOTIFY = ROOT / "mobile/android/src/api/jobNotifications.ts"
MOBILE_GENERATED = ROOT / "mobile/android/src/theme/wash.generated.ts"

def _web_vocabulary() -> dict:
    """The vocabulary embedded in the generated window.WASH object."""
    source = WEB_TOKENS.read_text(encoding="utf-8")
    match = re.search(r"var WASH = (\{.*?\n  \});\n", source, re.S)
    assert match, "WASH object not found in wash_tokens.js"
    return json.loads(match.group(1))["vocabulary"]


def _python_tables():
    # The terminal reads the shared vocabulary (mu/design/vocabulary.json).
    vocab = load_vocabulary()
    status = {k: (v["glyph"], v["label"]) for k, v in vocab["status"].items()}
    assert status == {k: (v["icon"], v["label"]) for k, v in theme.STATUS.items()}
    state_status = {k: v["status"] for k, v in vocab["job_state"].items()}
    state_label = {k: v["label"] for k, v in vocab["job_state"].items()}
    return status, state_status, state_label


def test_every_job_state_has_a_status_and_label():
    _, state_status, state_label = _python_tables()
    states = {s.value for s in JobState}
    assert set(state_status) == states
    assert set(state_label) == states


def test_web_reads_the_shared_vocabulary_and_has_no_local_tables():
    """Wash spec §3.1/B8: /work reads window.WASH instead of copying the tables."""
    py_status, py_state_status, py_state_label = _python_tables()
    web = _web_vocabulary()
    assert {k: (v["glyph"], v["label"]) for k, v in web["status"].items()} == py_status
    assert {k: v["status"] for k, v in web["job_state"].items()} == py_state_status
    assert {k: v["label"] for k, v in web["job_state"].items()} == py_state_label
    assert web == load_vocabulary()
    # Every page loads the generated tokens before its scripts; /work is on that layout.
    assert '<script src="/static/js/wash_tokens.js"></script>' in WEB_LAYOUT.read_text(encoding="utf-8")
    assert '{% extends "_layout.html" %}' in WEB_WORK.read_text(encoding="utf-8")
    view = WEB_VIEW.read_text(encoding="utf-8")
    assert "const WASH = window.WASH;" in view and "const VOCAB = WASH.vocabulary;" in view
    assert "WASH.stateStyle(state).status" in view and "WASH.stateStyle(state).label" in view
    for table in ("severity", "verdict", "coverage"):
        assert f"VOCAB.{table}" in view, table
    # No hand-written vocabulary tables survive.
    for name in ("STATUS", "STATE_STATUS", "STATE_LABEL", "SEVERITY", "VERDICT", "COVERAGE"):
        assert not re.search(rf"\bconst {name}\s*=", view), name
    assert not re.search(r"\{\s*icon\s*:\s*'", view)
    assert not re.search(r"\b(major|minor|blocker)\s*:\s*'(danger|warn|muted)'", view)


def test_provisioning_is_labelled_preparing_everywhere():
    assert theme.state_style("provisioning")["label"] == "preparing"
    assert _web_vocabulary()["job_state"]["provisioning"]["label"] == "preparing"
    # Android reads the label from the generated vocabulary (no local table).
    assert '"provisioning": {\n      "status": "active",\n      "label": "preparing"' in MOBILE_GENERATED.read_text(encoding="utf-8")


def test_mobile_reads_the_generated_vocabulary_and_has_no_local_tables():
    """Wash spec §3.1/B8: Android imports the shared vocabulary instead of copying it."""
    status = MOBILE_STATUS.read_text(encoding="utf-8")
    assert "from './wash.generated'" in status
    assert "vocabulary.status" in status and "vocabulary.job_state" in status
    # No hand-written glyph/label tables survive.
    assert "STATE_STATUS" not in status and "STATE_LABEL" not in status
    assert not re.search(r"\{\s*icon\s*:\s*'", status)
    assert not re.search(r"'(✓|●|◆|✗|○)'", status)
    # The generated module embeds exactly the shared vocabulary.
    generated = MOBILE_GENERATED.read_text(encoding="utf-8")
    vocab = json.loads((ROOT / "mu/design/vocabulary.json").read_text(encoding="utf-8"))
    for state, row in vocab["job_state"].items():
        assert f'"{state}": {{\n      "status": "{row["status"]}",\n      "label": "{row["label"]}"' in generated


def test_mobile_needs_you_states_are_the_waiting_states():
    source = MOBILE_NOTIFY.read_text(encoding="utf-8")
    # Derived from the vocabulary's waiting states, never a literal list.
    assert "NEEDS_YOU_STATES = new Set(WAITING_STATES)" in source
    assert not re.search(r"NEEDS_YOU_STATES\s*=\s*new Set\(\[", source)
    status = MOBILE_STATUS.read_text(encoding="utf-8")
    assert "WAITING_STATES = Object.keys(jobStates).filter(state => jobStates[state].status === 'waiting')" in status
    vocab = json.loads((ROOT / "mu/design/vocabulary.json").read_text(encoding="utf-8"))
    waiting = {s for s, status in _python_tables()[1].items() if status == "waiting"}
    assert {s for s, row in vocab["job_state"].items() if row["status"] == "waiting"} == waiting
