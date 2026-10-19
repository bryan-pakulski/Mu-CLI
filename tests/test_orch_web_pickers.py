"""Web pickers (documentation/plans/runbook_editor.md §4.1): the JS schema validator agrees with
``node/schema.py`` on one generated case list, the selector builder speaks the grammar of
``selectors.py``, and the run wizard, schedule form and test dialogs use the shared pickers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mu.orchestration import selectors
from mu.orchestration.node import schema as jschema

JS = Path(__file__).resolve().parent.parent / "mu" / "gui" / "static" / "js" / "orchestration"


def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


STRING = {"type": "string", "minLength": 2, "maxLength": 5, "pattern": "^[a-z]+$"}
NUMBER = {"type": "number", "minimum": 1, "maximum": 10, "exclusiveMinimum": 0, "exclusiveMaximum": 11}
OBJECT = {"type": "object", "properties": {"name": STRING, "count": {"type": "integer", "minimum": 0},
                                           "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True,
                                                    "maxItems": 3, "minItems": 1}},
          "required": ["name"], "additionalProperties": False}


def _cases():
    """Every keyword the subset supports, on both sides of its boundary, plus type mismatches."""
    schemas = [
        ({"type": "string"}, ["x", 1, None, True, [], {}]),
        (STRING, ["ab", "a", "abcdef", "AB", "abc", "ab1"]),
        ({"type": "integer"}, [1, -3, 1.5, "1", True, None]),
        ({"type": "integer", "minimum": 1, "maximum": 90}, [1, 0, 90, 91, 45]),
        (NUMBER, [1, 0.5, 10, 10.5, 0, 11, 5]),
        ({"type": "boolean"}, [True, False, 0, "true"]),
        ({"type": "null"}, [None, 0, ""]),
        ({"type": ["string", "null"]}, ["x", None, 3]),
        ({"enum": ["a", "b", 3]}, ["a", "c", 3, 4, None]),
        ({"const": "fixed"}, ["fixed", "other"]),
        ({"const": 5}, [5, 6]),
        ({"type": "array", "items": {"type": "integer", "maximum": 3}, "minItems": 1, "maxItems": 2},
         [[], [1], [1, 2, 3], [4], ["x"], [1, 9]]),
        ({"type": "array", "uniqueItems": True}, [[1, 2], [1, 1], [{"a": 1}, {"a": 1}], ["x", "y", "x"]]),
        (OBJECT, [{"name": "ab"}, {}, {"name": "ab", "extra": 1}, {"name": "A"}, {"name": "ab", "count": -1},
                  {"name": "ab", "tags": []}, {"name": "ab", "tags": ["a", "a"]}, {"name": "ab", "tags": [1]},
                  {"name": "ab", "tags": ["a", "b", "c", "d"]}, "not an object"]),
        ({"type": "object", "additionalProperties": {"type": "integer"}}, [{"a": 1}, {"a": "x"}]),
        ({"type": "object", "properties": {"inner": OBJECT}}, [{"inner": {"name": "ok"}}, {"inner": {"zzz": 1}}]),
        ({"type": "string", "pattern": "^v\\d+\\.\\d+$"}, ["v1.2", "1.2", "v1"]),
        ({"type": "string", "enum": ["it's"]}, ["it's", "its"]),
        ({}, [1, "x", None, [], {}]),
    ]
    return [{"schema": s, "value": v} for s, values in schemas for v in values]


def test_js_schema_validator_matches_the_node_validator():
    cases = _cases()
    expected = [jschema.validate(c["value"], c["schema"], "params") for c in cases]
    got = _node("const S = require(%s); const cases = %s;"
                "process.stdout.write(JSON.stringify(cases.map(c => S.validate(c.value, c.schema, 'params'))));"
                % (json.dumps(str(JS / "schema.js")), json.dumps(cases)))
    mismatches = [(c, e, g) for c, e, g in zip(cases, expected, got) if e != g]
    assert not mismatches, mismatches[:5]
    assert any(expected) and not all(expected)          # the case list exercises both outcomes


def test_js_defaults_and_kinds():
    schema = {"type": "object", "properties": {"days": {"type": "integer", "default": 7},
                                               "mode": {"enum": ["a", "b"], "default": "a"},
                                               "tags": {"type": "array", "items": {"type": "string"}},
                                               "on": {"type": "boolean"}, "conf": {"type": "object"},
                                               "name": {"type": "string"}}}
    out = _node("const S = require(%s); const s = %s;"
                "process.stdout.write(JSON.stringify({filled: S.applyDefaults({days: 3}, s),"
                " kinds: Object.fromEntries(Object.entries(s.properties).map(([k, p]) => [k, S.kindOf(p)]))}));"
                % (json.dumps(str(JS / "schema.js")), json.dumps(schema)))
    assert out["filled"] == jschema.apply_defaults({"days": 3}, schema)
    assert out["kinds"] == {"days": "integer", "mode": "enum", "tags": "list", "on": "boolean", "conf": "json",
                            "name": "string"}


SELECTORS = ["env=prod", "env==prod,role!=db", "role in (web,api),!legacy", "env notin (dev,test),gpu",
             "a.b/c=x:y", "", "*"]


def test_selector_builder_speaks_the_selector_grammar():
    prelude = ("global.window = {OrchUI: {esc: s => String(s), icon: () => '', uid: p => p, debounce: f => f},"
               " OrchAPI: {}, OrchSchema: {}}; global.document = {};")
    out = _node(prelude + (JS / "pickers.js").read_text(encoding="utf-8") +
                "\nconst P = window.OrchPickers; const sels = %s;"
                "process.stdout.write(JSON.stringify(sels.map(s => { const rows = P.parseSelector(s);"
                " return rows === null ? null : P.formatSelector(rows); })));" % json.dumps(SELECTORS + ["env in ("]))
    for text, formatted in zip(SELECTORS, out):
        assert formatted is not None, text
        assert selectors.parse(formatted) == selectors.parse(text), (text, formatted)
    assert out[-1] is None                                # unparseable: the builder falls back to text


def test_pickers_are_accessible_comboboxes():
    src = (JS / "pickers.js").read_text(encoding="utf-8")
    for needle in ('role="combobox"', 'aria-autocomplete="list"', 'role="listbox"', 'role="option"',
                   "aria-activedescendant", "aria-expanded", "catalog/options?purpose=", "api.get('labels')",
                   "api.cmd('previewSelector'", "Orch.subscribe"):
        assert needle in src, needle


def test_wizard_schedule_and_test_dialogs_use_the_pickers():
    runs = (JS / "runs.js").read_text(encoding="utf-8")
    assert "P.combobox(el.querySelector('[data-target]'), { purpose: 'run'" in runs
    assert "P.selectorBuilder(el.querySelector('[data-where]')" in runs and "params.rebuild(schema)" in runs
    assert "tools?all=false" not in runs                  # no catalog list baked into the page
    settings = (JS / "settings.js").read_text(encoding="utf-8")
    assert "purpose: 'schedule'" in settings and "P.selectorBuilder(" in settings and "run: run|test" in settings
    studio = (JS / "studio.js").read_text(encoding="utf-8")
    assert "P.nodePicker(form.querySelector('[data-node]'), { test: true" in studio
    html = (JS.parent.parent.parent / "templates" / "orchestration.html").read_text(encoding="utf-8")
    order = [html.index(f"/static/js/orchestration/{name}.js") for name in ("api", "ui", "schema", "pickers", "runs",
                                                                           "view")]
    assert order == sorted(order)


def test_discovery_chat_offers_providers_and_their_models():
    studio = (JS / "studio.js").read_text(encoding="utf-8")
    # The model is chosen from what this GUI can reach, not typed from memory.
    assert "api.get('/api/providers')" in studio
    assert "api.get(`/api/providers/${encodeURIComponent(provider)}/models`)" in studio
    assert "p.configured ? '' : ' disabled'" in studio            # unconfigured providers can't be picked
    assert "type the model name" in studio                        # free text when a provider can't list models
    assert "localStorage.setItem(LAST_MODEL" in studio
