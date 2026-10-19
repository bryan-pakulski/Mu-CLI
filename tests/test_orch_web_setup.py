"""The node setup wizard's web UI (documentation/plans/runbook_editor.md §5): structure, wiring
and the pure helpers in static/js/orchestration/setup.js, run under node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "mu" / "gui" / "static" / "js" / "orchestration"
SETUP = (JS / "setup.js").read_text(encoding="utf-8")


def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    prelude = ("global.window = {OrchAPI: {}, OrchUI: {esc: s => String(s), icon: () => ''}, Orch: {routes: {}}};"
               "global.localStorage = {getItem: () => null, setItem: () => {}};")
    out = subprocess.run([node, "-e", prelude + SETUP + "\nconst S = window.Orch.setup;\n" + script],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_route_and_entry_points_are_registered():
    template = (ROOT / "mu" / "gui" / "templates" / "orchestration.html").read_text(encoding="utf-8")
    assert template.index("orchestration/nodes.js") < template.index("orchestration/setup.js") < template.index("orchestration/view.js")
    assert "Orch.routes.setup = { nav: 'nodes'" in SETUP
    nodes = (JS / "nodes.js").read_text(encoding="utf-8")
    assert 'data-variant="primary" href="#/setup"' in nodes and "Quick enrol" in nodes
    view = (JS / "view.js").read_text(encoding="utf-8")
    assert 'href="#/setup"' in view and "orch:setup" in view and "Quick enrol" in view


def test_wizard_uses_the_backend_it_needs():
    for part in ("api.get('network/interfaces')", "api.getSecure(`setup/", "'setNetwork'", "'createJoinToken'",
                 "'overlayAddPeer'", "'acceptNode'", "'setNodeLabels'", "'setNodeCanary'", "'verifyNode'",
                 "'installStarterPack'", "api.download(`setup/", "orch.setup.last"):
        assert part in SETUP, part
    # The live token never goes through innerHTML unescaped and downloads are digest-checked.
    assert "r.sha256 === expect" in SETUP
    assert "setInterval(" in SETUP and "clearInterval(timer)" in SETUP   # slow fallback only, cleared on dispose


def test_step_progression():
    out = _node("""
        const node = (status, last_seen) => ({status, last_seen});
        console.log(JSON.stringify([
          S.stepFor({}), S.stepFor({endpointReady: true}),
          S.stepFor({endpointReady: true, setup: {used_at: null}}),
          S.stepFor({endpointReady: true, setup: {used_at: null}, waiting: true}),
          S.stepFor({setup: {used_at: 1, node: null}}), S.stepFor({setup: {used_at: 1, node: node('pending')}}),
          S.stepFor({setup: {used_at: 1, node: node('active')}}),
          S.stepFor({setup: {used_at: 1, node: node('active')}, heartbeatOk: true}),
          S.stepFor({setup: {used_at: 1, node: node('active')}, heartbeatOk: true, roundTripOk: true}),
          S.heartbeatOk(node('active', 100), 120), S.heartbeatOk(node('active', 100), 200),
          S.heartbeatOk(node('pending', 100), 101),
          S.heartbeatOk({...node('active', 100), accepted_at: 110}, 115),   // polled before acceptance only
          S.heartbeatOk({...node('active', 112), accepted_at: 110}, 115),
        ]));
    """)
    assert out == [1, 2, 3, 4, 5, 5, 6, 6, 7, True, False, False, False, True]


def _node_refusals():
    """sys.facts refused by real node policies (the wording the wizard's hints must recognise)."""
    import time as _time
    from mu.orchestration.node import policy as node_policy
    from mu.orchestration.starter import load_starter_tools
    facts = next(m for m, _f in load_starter_tools() if m["id"] == "sys.facts")
    out = {}
    for key, text in (("denied", 'denied_tools: ["sys.*"]'), ("allowed", 'allowed_tools: ["app.*"]'),
                      ("category", "accept_categories: [maintenance]"), ("runtime", "allowed_runtimes: [bash]"),
                      ("run_as", 'allowed_run_as: ["root"]')):
        decision = node_policy.decide({"check_mode": False}, facts, node_policy.parse_policy(text), now=_time.time())
        assert not decision.allowed, key
        out[key] = decision.reason
    return out


def test_failure_hints_match_the_node_policy_wording():
    reasons = _node_refusals()
    hints = _node("console.log(JSON.stringify(Object.fromEntries(Object.entries(%s).map(([k, r]) => [k, S.failureHint(r)]))));"
                  % json.dumps(reasons))
    assert "denied_tools" in hints["denied"] and "allowed_tools" in hints["allowed"]
    assert "accept_categories" in hints["category"] and "allowed_runtimes" in hints["runtime"]
    assert "allowed_run_as" in hints["run_as"]


def test_policy_warnings_and_failure_hints():
    out = _node("""
        console.log(JSON.stringify({
          ro: S.policyWarnings({max_effect: 'read_only', sandbox: 'none'}, []).map(w => w.tone),
          bad: S.policyWarnings({max_effect: 'mutating'}, ['line 3: bad']).map(w => w.text.includes('invalid')),
          none: S.policyWarnings({}, []).length,
          tools: S.failureHint('refused by node: tool not in allowed_tools'),
          cat: S.failureHint('refused by node: category inventory not accepted'),
          other: S.failureHint('exit 3'),
          reach: S.reachCommand('https://10.0.0.5:30312'),
          exec: S.mainExec({grid: [{cells: [{exec: {id: 'pre'}}, {exec: {id: 'main'}}]}]}).id,
        }));
    """)
    assert out["ro"] == ["caution", "caution"] and out["bad"] == [True] and out["none"] == 1
    assert "allowed_tools" in out["tools"] and "accept_categories" in out["cat"] and "journalctl" in out["other"]
    assert out["reach"].startswith("curl -sk") and "https://10.0.0.5:30312/api/orchestration/node/install.sh" in out["reach"]
    assert out["exec"] == "main"
