"""Runbook editor web UI (documentation/plans/runbook_editor.md §4, R2/R3): the pure editor model
round-trips specs losslessly (form → spec unchanged), the inputs table generates the schema
subset, and the page wiring (routes, server-side conversion, base_digest) is in place."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "mu" / "gui" / "static" / "js" / "orchestration"
RUNBOOKS = (JS / "runbooks.js").read_text(encoding="utf-8")
STUDIO = (JS / "studio.js").read_text(encoding="utf-8")
CATALOG = (JS / "catalog.js").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "mu" / "gui" / "templates" / "orchestration.html").read_text(encoding="utf-8")

APP_DEPLOY = {"id": "app.deploy", "title": "Deploy app", "inputs": {
    "type": "object", "properties": {"msg": {"type": "string", "maxLength": 20, "default": "ok"}},
    "additionalProperties": False},
    "steps": [
        {"name": "check", "tool": "app.check@1", "params": {"message": "pre"}},
        {"name": "change", "tool": "app.change@1", "on_failure": "stop",
         "params": {"message": "${{ node.labels.payload }}"}},
        {"name": "verify", "tool": "app.check@1", "when": "steps.change.state == 'succeeded'",
         "params": {"message": "${{ steps.change.outputs.message }}"}},
    ]}
APP_TWO = {"id": "app.two", "title": "two", "steps": [
    {"name": "first", "tool": "app.change@1", "params": {"message": "fine"}},
    {"name": "second", "tool": "app.change@1", "on_failure": "rollback",
     "params": {"message": "${{ node.labels.payload }}"}}]}
WEB_CHECK = {"id": "web.check", "title": "Check web", "inputs": {
    "type": "object", "properties": {"msg": {"type": "string", "default": "ok"}}, "additionalProperties": False},
    "steps": [{"name": "check", "tool": "app.check@1", "params": {"message": "${{ inputs.msg }}"}},
              {"name": "again", "tool": "app.check@1", "when": "steps.check.outputs.free_pct < 20",
               "params": {"message": "${{ steps.check.outputs.message }}", "keep_days": 30}}]}
RICH = {"id": "ops.rich", "title": "Rich", "summary": "Everything at once", "version": 3, "effect": "mutating",
        "inputs": {"type": "object", "required": ["env", "keep"], "additionalProperties": False, "properties": {
            "env": {"type": "string", "enum": ["dev", "prod"], "description": "Where"},
            "keep": {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
            "host": {"type": "string", "pattern": "^[a-z0-9.-]+$", "maxLength": 63},
            "tags": {"type": "array", "items": {"type": "string"}, "default": []}}},
        "steps": [{"name": "a", "tool": "x.y@2", "when": "inputs.env == 'prod'", "on_failure": "continue",
                   "nodes": "role in (web,api),!legacy",
                   "params": {"opts": {"nested": [1, {"deep": "${{ inputs.host }}"}], "flag": True}, "n": 3}},
                  {"name": "b", "tool": "x.z@1", "params": {}, "when": "", "nodes": "", "extra_key": "kept"}]}
SPECS = [APP_DEPLOY, APP_TWO, WEB_CHECK, RICH]


def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    prelude = f"const R = require({json.dumps(str(JS / 'runbooks.js'))});\n"
    out = subprocess.run([node, "-e", prelude + script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_specs_survive_the_editor_model_unchanged():
    result = _node(f"""
        const specs = {json.dumps(SPECS)};
        const out = specs.map(s => {{
            const m = R.specToModel(s);
            m.inputs = m.inputs === undefined ? undefined : R.rowsToInputs(R.inputsToRows(m.inputs), m.inputs);
            return R.modelToSpec(m);
        }});
        console.log(JSON.stringify(out));
    """)
    for before, after in zip(SPECS, result):
        assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)


def test_inputs_table_edits_generate_the_schema_subset():
    result = _node(f"""
        const base = {json.dumps(RICH['inputs'])};
        const rows = R.inputsToRows(base);
        rows[1].edits.default = '30';
        rows[2].edits.maxLength = '';
        rows[0].required = false;
        rows.push({{name: 'mode', raw: {{type: 'string'}}, required: true, edits: {{enum: 'a, b', description: 'how'}}}});
        const fresh = R.rowsToInputs([{{name: 'x', raw: {{}}, required: false, edits: {{type: 'number', default: '2.5'}}}}], undefined);
        console.log(JSON.stringify({{edited: R.rowsToInputs(rows, base), fresh}}));
    """)
    edited = result["edited"]
    assert edited["properties"]["keep"]["default"] == 30
    assert "maxLength" not in edited["properties"]["host"]
    assert edited["required"] == ["keep", "mode"]
    assert edited["properties"]["mode"] == {"type": "string", "enum": ["a", "b"], "description": "how"}
    assert result["fresh"] == {"type": "object", "properties": {"x": {"type": "number", "default": 2.5}},
                               "additionalProperties": False}


def test_new_steps_only_carry_what_was_set():
    result = _node("""
        const m = R.specToModel({id: 'a.b', title: 't', steps: []});
        m.steps.push(R.stepToModel({name: 'step1', tool: ''}));
        m.steps[0].tool = 'sys.facts@1';
        console.log(JSON.stringify(R.modelToSpec(m)));
    """)
    assert result == {"id": "a.b", "title": "t", "steps": [{"name": "step1", "tool": "sys.facts@1"}]}


def test_routes_and_scripts_are_wired():
    assert '<script src="/static/js/orchestration/runbooks.js"></script>' in TEMPLATE
    assert TEMPLATE.index("pickers.js") < TEMPLATE.index("runbooks.js")
    assert "if (route.parts[0] === 'runbook') return Orch.runbooks.renderEditor(el, route, opts);" in STUDIO
    assert "#/studio/runbook/new" in STUDIO and "#/studio/runbook/new" in CATALOG
    assert "Orch.runbooks = { ...pure, renderEditor, renderNew, newRunbook, testDialog, upgradeDialog" in RUNBOOKS
    # The catalog page hands its edit/test dialogs and the new-version result to the editor.
    assert "edit: () => Orch.go(editorHref)" in CATALOG and "Orch.runbooks?.testDialog(r)" in CATALOG
    assert "action.id === 'newRunbookVersion' && result?.data?.ref" in CATALOG


def test_editor_uses_the_server_and_never_overwrites_silently():
    # Every save carries the loaded digest; a 409 shows the stale banner instead of retrying.
    assert "api.cmd('saveRunbookDraft', { spec, runbook_id: r.runbook_id, base_digest: baseDigest })" in RUNBOOKS
    assert "err.status === 409 && err.data?.conflict" in RUNBOOKS
    assert "Keep mine" in RUNBOOKS and "Reload (discard my edits)" in RUNBOOKS
    # Checks and view switching go through validateRunbook (no client-side YAML), cancelling stale requests.
    assert "api.cmd('validateRunbook', { spec }, { signal: ctrl.signal })" in RUNBOOKS
    assert "ui.debounce(validate, 400)" in RUNBOOKS
    assert "Fix the YAML error first" in RUNBOOKS
    # Live changes never replace local edits; a draft whose YAML doesn't parse reopens on the YAML tab.
    assert "if (!dirty) { Orch.refresh(); return; }" in RUNBOOKS
    assert "let tab = r.source ? 'yaml'" in RUNBOOKS
    assert "beforeunload" in RUNBOOKS and "clearInterval(ageTimer)" in RUNBOOKS
    # Keyboard reordering and the bulk upgrade / canary test commands.
    assert "e.altKey" in RUNBOOKS and "ArrowUp" in RUNBOOKS
    for command in ("upgradeRunbookTools", "newRunbookVersion", "proposeRunbookTest"):
        assert f"'{command}'" in RUNBOOKS, command


def test_interpolated_text_is_escaped():
    # Spec values reach innerHTML only through esc(): names, titles, refs, problem messages.
    for needle in ("${esc(s.name)}", "${esc(model.title)}", "${esc(p.message)}", "${esc(r.ref)}", "${esc(yamlText)}",
                   "${esc(s.when)}", "${esc(row.name)}"):
        assert needle in RUNBOOKS, needle
    # (completion values like `steps.${s.name}.state` are data, escaped where pickers.js renders them)
    import re
    for name in ("s.name", "model.title", "p.message", "yamlText", "row.name", "r.title", "p.path"):
        assert not re.search(r'[>"]\$\{' + re.escape(name) + r'\}', RUNBOOKS), name
