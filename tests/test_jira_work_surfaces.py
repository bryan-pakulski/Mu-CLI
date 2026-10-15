"""Work page: Job settings (Jira link) + Jira ticket browser markup/JS contract."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "mu/gui/templates/work.html").read_text(encoding="utf-8")
JS = (ROOT / "mu/gui/static/js/work_jira.js").read_text(encoding="utf-8")
WORK_JS = (ROOT / "mu/gui/static/js/work.js").read_text(encoding="utf-8")


def test_assets_wired_into_work_page():
    assert '/static/js/work_jira.js' in TEMPLATE
    # Wash W2: /work links its page bundle, which imports work_jira.css.
    assert '/static/css/wash/patterns/work.css' in TEMPLATE
    bundle = (ROOT / "mu/gui/static/css/wash/patterns/work.css").read_text(encoding="utf-8")
    assert '@import url("../../work_jira.css") layer(legacy);' in bundle
    assert (ROOT / "mu/gui/static/css/work_jira.css").exists()


def test_header_buttons_are_labelled():
    for btn_id, label in (("work-jira-settings-open", "Job settings"), ("work-jira-open", "Browse Jira tickets")):
        assert re.search(rf'id="{btn_id}"[^>]*aria-label="{label}"', TEMPLATE)


def test_every_element_the_script_uses_exists():
    ids = set(re.findall(r"\$\('([^']+)'\)", JS)) | set(re.findall(r"getElementById\('([^']+)'\)", JS))
    missing = sorted(i for i in ids if f'id="{i}"' not in TEMPLATE)
    assert not missing, missing


def test_token_fields_are_password_inputs_like_ollama_key():
    for field in ("jira-api-token", "jira-pat"):
        assert re.search(rf'id="{field}"[^>]*type="password"', TEMPLATE)
    # Same UX as the Ollama key: placeholder signals a stored secret, value never prefilled.
    assert "token set — type to replace" in JS
    assert "els.token.value = ''" in JS


def test_browser_shows_linked_job_and_status_and_imports():
    assert "data-open-job" in JS and 'class="work-status"' in JS
    assert "/api/jobs/jira/tickets" in JS and "/api/jobs/jira/import" in JS
    assert "/api/jobs/jira/settings" in JS and "/api/jobs/jira/test" in JS


def test_work_js_exposes_hook_for_jira_script():
    assert "window.MuWork" in WORK_JS and "selectJob" in WORK_JS
