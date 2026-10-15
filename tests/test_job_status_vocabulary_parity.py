"""The job status vocabulary must read the same on every surface.

The terminal (``mu/ui/jobs/theme.py``), web (``work/view.js``) and Android
(``theme/status.ts``, ``api/jobNotifications.ts``) each carry a copy of the
state → status → label tables. The copies had drifted: the terminal
labelled ``provisioning`` as "provisioning" while web and mobile said
"preparing". Until a shared vocabulary file replaces the copies (Wash
spec §3.1), this test keeps them identical and complete.
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
MOBILE_STATUS = ROOT / "mobile/android/src/theme/status.ts"
MOBILE_NOTIFY = ROOT / "mobile/android/src/api/jobNotifications.ts"
MOBILE_GENERATED = ROOT / "mobile/android/src/theme/wash.generated.ts"

_PAIR = re.compile(r"""['"]?(\w+)['"]?\s*:\s*['"]([^'"]*)['"]""")


def _object_literal(source: str, name: str) -> dict[str, str]:
    match = re.search(rf"\b{name}\b[^=]*=\s*\{{(.*?)\}};", source, re.S)
    assert match, f"{name} table not found"
    return dict(_PAIR.findall(match.group(1)))


def _status_table(source: str) -> dict[str, tuple[str, str]]:
    match = re.search(r"\bSTATUS\b[^=]*=\s*\{(.*?)\};", source, re.S)
    assert match, "STATUS table not found"
    rows = re.findall(
        r"(\w+)\s*:\s*\{\s*icon\s*:\s*'([^']*)'\s*,\s*tone\s*:\s*'\w+'\s*,\s*label\s*:\s*'([^']*)'",
        match.group(1),
    )
    return {key: (icon, label) for key, icon, label in rows}


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


def test_web_tables_match_the_terminal():
    py_status, py_state_status, py_state_label = _python_tables()
    for path in (WEB_VIEW,):
        source = path.read_text(encoding="utf-8")
        assert _status_table(source) == py_status, path.name
        assert _object_literal(source, "STATE_STATUS") == py_state_status, path.name
        assert _object_literal(source, "STATE_LABEL") == py_state_label, path.name


def test_provisioning_is_labelled_preparing_everywhere():
    assert theme.state_style("provisioning")["label"] == "preparing"
    for path in (WEB_VIEW,):
        labels = _object_literal(path.read_text(encoding="utf-8"), "STATE_LABEL")
        assert labels["provisioning"] == "preparing", path.name
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
