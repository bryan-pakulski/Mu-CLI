"""Web surfaces for running a job in a container template (documentation/plans/job_templates.md §3):
the shared "Run in" picker, the New job sheet and Jira import payloads, scheduled-sync "Run in"
rows, the template card's "Used for jobs" section, and the job page's setup progress and
unverified checks."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "mu" / "gui" / "static"
HTML = (ROOT / "mu" / "gui" / "templates" / "work.html").read_text(encoding="utf-8")
PICKER_PATH = STATIC / "js" / "work" / "run_in_picker.js"
PICKER = PICKER_PATH.read_text(encoding="utf-8")
SHELL = (STATIC / "js" / "work.js").read_text(encoding="utf-8")
JIRA = (STATIC / "js" / "work_jira.js").read_text(encoding="utf-8")
VIEW = (STATIC / "js" / "work" / "view.js").read_text(encoding="utf-8")
ENV = (STATIC / "js" / "work" / "environments.js").read_text(encoding="utf-8")
CONTAINERS = (STATIC / "js" / "containers.js").read_text(encoding="utf-8")
WORK_CSS = (STATIC / "css" / "wash" / "patterns" / "work.css").read_text(encoding="utf-8")
CONTAINERS_CSS = (STATIC / "css" / "wash" / "patterns" / "containers.css").read_text(encoding="utf-8")


# ------------------------------------------------------------------ picker contract (§3.1)


def test_picker_loads_after_the_environments_editor_and_before_the_shell():
    assert HTML.index("/static/js/work/environments.js") < HTML.index("/static/js/work/run_in_picker.js") \
        < HTML.index("/static/js/work.js")
    assert "window.MuRunInPicker = {create, preselect, setupSummary, commandKey, LAST_KEY}" in PICKER


def test_picker_groups_options_and_offers_advanced():
    for part in ("'Default runtime'", '>Templates</div>', "Environments (snapshotted at start)", "Advanced…",
                 'role="listbox"', 'role="option"', "aria-haspopup=\"listbox\"", "data-run-in-advanced"):
        assert part in PICKER, part
    # Template rows: description, setup summary, size and GPU; Advanced… is the existing editor.
    assert "[t.description, setupSummary(t.job_setup), t.size, t.gpus ? 'GPU' : '']" in PICKER
    assert "E.editor({api, ui, title:'Build environments for the new job'" in PICKER


def test_picker_fit_warning_allows_the_domain_after_a_confirm():
    assert "can't reach ${missing.join(', ')}" in PICKER and "data-run-in-allow" in PICKER
    assert "await ui.confirm(" in PICKER
    assert "`/api/containers/templates/${encodeURIComponent(name)}`, {method:'PATCH', body:{egress_allow:[...(t.egress_allow || []), ...missing]}}" in PICKER


def test_picker_payload_and_remember():
    assert "{template:choice.template, ...(choice.remember && isLocal() ? {remember_template:true} : {})}" in PICKER
    assert "return {environments:[choice.spec]}" in PICKER
    assert "Remember for this workspace" in PICKER and "data-run-in-remember" in PICKER
    # The data source: GET /api/jobs/environments with the workspace (fit + remembered).
    assert "`/api/jobs/environments?${q}`" in PICKER and "workspace:isRepoUrl(ctx.workspace) ? '' : ctx.workspace" in PICKER


def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    prelude = ("const store = {}; global.localStorage = {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }};"
               "global.window = {}; global.document = {};")
    out = subprocess.run([node, "-e", prelude + PICKER + "\n" + script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_picker_setup_summary_and_preselection_order():
    result = _node("""
        const P = window.MuRunInPicker;
        const info = {templates: [{name: 'rsq-node-build'}, {name: 'gpu'}],
                      environments: [{name: 'rust-build', container: 'mucli-rust-build', spec: 'environment:mucli-rust-build'}],
                      resolved: {source: 'session', bindings: [{ref: 'mucli-rust-build'}]}};
        localStorage.setItem(P.LAST_KEY, JSON.stringify({'/w': 'gpu'}));
        const out = {
          summary: P.setupSummary(['npm --prefix resq-management ci', 'npm --prefix resq-frontend ci', 'npm ci', 'cargo fetch --manifest-path x/Cargo.toml']),
          remembered: P.preselect({...info, remembered: {source: 'template:rsq-node-build'}}, '/w'),
          session: P.preselect(info, '/w'),
          last: P.preselect({...info, resolved: {source: 'default', bindings: []}}, '/w'),
          fallback: P.preselect({...info, resolved: {}}, '/other'),
          unknownRemembered: P.preselect({templates: [], remembered: {source: 'template:gone'}}, '/x'),
        };
        console.log(JSON.stringify(out));
    """)
    assert result["summary"] == "setup: npm ci ×3, cargo fetch"
    assert result["remembered"] == {"kind": "template", "template": "rsq-node-build", "remembered": True}
    assert result["session"] == {"kind": "environment", "spec": "environment:mucli-rust-build", "name": "rust-build"}
    assert result["last"] == {"kind": "template", "template": "gpu"}
    assert result["fallback"] == {"kind": "default"}
    assert result["unknownRemembered"] == {"kind": "default"}


# ------------------------------------------------------------------ surfaces (§3.2)


def test_new_job_sheet_uses_the_picker_and_sends_execution_template():
    field = HTML[HTML.index('id="job-environments-field"'):HTML.index('id="job-environments-hint"')]
    assert ">Run in<" in field and 'id="job-run-in"' in field and ">Advanced…<" in field
    assert "window.MuRunInPicker.create(runInHost" in SHELL
    assert "if (runIn) Object.assign(execution, runIn.payload());" in SHELL
    # Advanced… (the old Change) opens the multi-environment editor through the picker.
    assert "if (runIn) { await runIn.advanced(); syncRunIn(); return; }" in SHELL
    assert "runIn?.commit();" in SHELL  # device memory of the last template for this workspace


def test_jira_import_dialog_has_run_in_and_sends_defaults_execution():
    dialog = SHELL[SHELL.index("async function chooseImportTarget("):SHELL.index("async function chooseSession(")]
    assert dialog.index('id="work-import-workspace"') < dialog.index('id="work-import-run-in-field"') < dialog.index('id="work-import-session"')
    assert "data-import-run-in" in dialog and "idPrefix: 'work-import-run-in'" in dialog
    assert "result.defaults.execution = runInPayload" in dialog
    assert "body.defaults = { ...target.defaults };" in JIRA  # carries defaults.execution.template


def test_jira_watch_entries_are_rows_with_a_run_in_select():
    assert 'data-tr="watch-queries"' in JIRA and 'data-tr="watch-rows"' in JIRA and 'data-tr="watch-add"' in JIRA
    for key in ('data-watch-key="tracker"', 'data-watch-key="query"', 'data-watch-key="workspace_root"',
                'data-watch-key="template"', "data-watch-remove"):
        assert key in JIRA, key
    assert "Run in for query" in JIRA and "<option value=\"\">Default</option>" in JIRA
    assert "template: w.template || ''" in JIRA and "/api/jobs/environments?images=false" in JIRA
    assert "<textarea data-tr=\"watch-queries\"" not in JIRA


def test_template_card_used_for_jobs_section():
    assert "${templateJobsSection(item)}" in CONTAINERS and "bindTemplateJobs();" in CONTAINERS
    for part in ("Used for jobs", "Used by ${used} job", "Job setup commands", "Network allowlist", "data-setup-add",
                 "data-setup-remove", "data-template-egress", "const MAX_SETUP = 20;"):
        assert part in CONTAINERS, part
    assert "`/api/containers/templates/${encodeURIComponent(name)}`, {method: 'PATCH'" in CONTAINERS
    assert "body: JSON.stringify({job_setup, egress_allow})" in CONTAINERS
    # The card keeps its "use" action; the editor never triggers it.
    assert "if (templateButton && event.target.closest('.w-env-jobs')) return;" in CONTAINERS
    assert 'id="template-${esc(item.name)}"' in CONTAINERS  # deep link from the job page
    assert ".w-env-jobs__summary" in CONTAINERS_CSS


def test_job_page_shows_setup_progress_and_unverified_checks():
    # Build environments panel: per-command setup status, Edit template link.
    assert "${setupBlock(x)}" in ENV and "data-env-setup" in ENV and "data-setup-status" in ENV
    assert "/containers#template-${encodeURIComponent(x.ref)}" in ENV
    # Activity/session feed: environment_setup events.
    assert "e.event_type === 'environment_setup'" in VIEW and "job setup [${p.binding || '?'}]" in VIEW
    # Checks tab: unverified uses the shared vocabulary status (? / caution), cause, hint and actions.
    assert "statusOf('unverified')" in VIEW and "data-unverified" in VIEW and "data-unrunnable" in VIEW
    assert "x.status === 'unrunnable' ? 'unverified'" in VIEW and "r.unrunnable_suites" in VIEW
    assert "job.actions.find(a => a.id === 'continueUnverified')" in VIEW and "data-prepare-template" in VIEW
    assert "Some checks could not run, so this change is unverified." in VIEW


def test_picker_and_rows_have_styles():
    for cls in (".work-runin__trigger", ".work-runin__menu", ".work-runin__warn", ".work-watch__row", ".work-setup-cmd",
                ".work-unverified"):
        assert cls in WORK_CSS, cls
