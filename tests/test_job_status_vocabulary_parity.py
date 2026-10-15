"""The job status vocabulary must read the same on every surface.

The terminal (``mu/ui/jobs/theme.py``), web (``work/view.js``) and Android
(``theme/status.ts``, ``api/jobNotifications.ts``) each carry a copy of the
state → status → label tables. The copies had drifted: the terminal
labelled ``provisioning`` as "provisioning" while web and mobile said
"preparing". Until a shared vocabulary file replaces the copies (Wash
spec §3.1), this test keeps them identical and complete.
"""

from __future__ import annotations

import re
from pathlib import Path

from mu.jobs.core.states import JobState
from mu.ui.jobs import theme

ROOT = Path(__file__).resolve().parents[1]
WEB_VIEW = ROOT / "mu/gui/static/js/work/view.js"
MOBILE_STATUS = ROOT / "mobile/android/src/theme/status.ts"
MOBILE_NOTIFY = ROOT / "mobile/android/src/api/jobNotifications.ts"

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
    status = {k: (v["icon"], v["label"]) for k, v in theme.STATUS.items()}
    return status, dict(theme._STATE_STATUS), dict(theme._STATE_LABEL)


def test_every_job_state_has_a_status_and_label():
    _, state_status, state_label = _python_tables()
    states = {s.value for s in JobState}
    assert set(state_status) == states
    assert set(state_label) == states


def test_web_and_mobile_tables_match_the_terminal():
    py_status, py_state_status, py_state_label = _python_tables()
    for path in (WEB_VIEW, MOBILE_STATUS):
        source = path.read_text(encoding="utf-8")
        assert _status_table(source) == py_status, path.name
        assert _object_literal(source, "STATE_STATUS") == py_state_status, path.name
        assert _object_literal(source, "STATE_LABEL") == py_state_label, path.name


def test_provisioning_is_labelled_preparing_everywhere():
    assert theme.state_style("provisioning")["label"] == "preparing"
    for path in (WEB_VIEW, MOBILE_STATUS):
        labels = _object_literal(path.read_text(encoding="utf-8"), "STATE_LABEL")
        assert labels["provisioning"] == "preparing", path.name


def test_mobile_needs_you_states_are_the_waiting_states():
    source = MOBILE_NOTIFY.read_text(encoding="utf-8")
    match = re.search(r"NEEDS_YOU_STATES\s*=\s*new Set\(\[(.*?)\]\)", source, re.S)
    assert match
    notify_states = set(re.findall(r"'(\w+)'", match.group(1)))
    waiting = {s for s, status in theme._STATE_STATUS.items() if status == "waiting"}
    assert notify_states == waiting
