"""Orchestration building blocks: access policy, selectors, schema, manifests, lint, runbook
expressions, schedules, overlay/bind checks, secrets and the audit chain."""

from __future__ import annotations

import datetime
import json
import os

import pytest

from mu.orchestration import access, lint, manifest as mf, overlay, runbook, schedules, selectors
from mu.orchestration.audit import verify, verify_store
from mu.orchestration.node import schema
from mu.orchestration.secrets import SecretsLocked, SecretStore
from mu.orchestration.store import OrchestrationStore

# --------------------------------------------------------------------- selectors


@pytest.mark.parametrize("expr,labels,expected", [
    ("", {}, True), ("*", {"a": "b"}, True), ("env=prod", {"env": "prod"}, True), ("env==prod", {"env": "dev"}, False),
    ("env!=prod", {"env": "dev"}, True), ("env!=prod", {}, True), ("env!=prod", {"env": "prod"}, False),
    ("role in (web,api)", {"role": "api"}, True), ("role in (web, api)", {"role": "db"}, False),
    ("role notin (web)", {"role": "db"}, True), ("role notin (web)", {}, True), ("canary", {"canary": "true"}, True),
    ("!legacy", {"legacy": "1"}, False), ("env=prod,role in (web,api),!legacy", {"env": "prod", "role": "web"}, True),
])
def test_selectors(expr, labels, expected):
    assert selectors.matches(expr, labels) is expected


@pytest.mark.parametrize("bad", ["env=", "env = prod = x", "role in ()", "role in (a", "a b c", "env=$(id)"])
def test_selector_errors(bad):
    with pytest.raises(selectors.SelectorError):
        selectors.parse(bad)


# --------------------------------------------------------------------- access policy

AI_RO = {"id": "sys.disk", "effect": "read_only", "category": "diagnostics", "approval": "none"}
MUT = {"id": "nginx.reload", "effect": "mutating", "category": "maintenance", "approval": "confirm"}
DESTRUCTIVE = {"id": "db.drop", "effect": "destructive", "category": "database", "approval": "confirm"}


def nodes(*label_sets):
    return [{"id": f"n{i}", "labels": labels} for i, labels in enumerate(label_sets)]


def test_default_rules_match_the_plan():
    rules = access.DEFAULT_RULES
    assert access.decide(rules, "ai:s", AI_RO, nodes({"env": "dev"})).level == "allow"
    assert access.decide(rules, "ai:s", AI_RO, nodes({"env": "prod"})).level == "confirm"
    assert access.decide(rules, "operator", AI_RO, nodes({"env": "prod"})).level == "allow"
    assert access.decide(rules, "ai:s", MUT, nodes({"env": "dev"})).level == "confirm"
    assert access.decide(rules, "operator", DESTRUCTIVE, nodes({"env": "dev"})).level == "typed"
    assert access.validate_rules(rules) == []


def test_strictest_node_wins_and_reports_the_rule():
    rules = access.DEFAULT_RULES + []
    d = access.decide(rules, "ai:s", AI_RO, nodes({"env": "dev"}, {"env": "prod"}))
    assert d.level == "confirm" and "prod" in d.rule
    deny = [{"name": "AI never touches the payment hosts", "match": {"actor": "ai", "nodes": "role=payments"},
             "decision": "deny"}] + access.DEFAULT_RULES
    d = access.decide(deny, "ai:s", AI_RO, nodes({"env": "dev"}, {"env": "dev", "role": "payments"}))
    assert d.denied and d.rule.startswith("AI never")
    assert "denied by rule" in d.summary()
    # The operator is not affected by an AI-only rule.
    assert access.decide(deny, "operator", AI_RO, nodes({"role": "payments"})).level == "allow"


def test_first_match_wins_and_fallback_is_confirm():
    rules = [{"name": "allow all", "match": {}, "decision": "allow"},
             {"name": "never reached", "match": {}, "decision": "deny"}]
    assert access.decide(rules, "ai", AI_RO, nodes({})).rule == "allow all"
    assert access.decide([], "ai", AI_RO, nodes({})).level == "confirm"


def test_manifest_approval_and_destructive_floors():
    allow_all = [{"name": "allow all", "match": {}, "decision": "allow"}]
    assert access.decide(allow_all, "operator", MUT, nodes({})).level == "confirm"
    assert access.decide(allow_all, "operator", DESTRUCTIVE, nodes({})).level == "typed"


def test_tool_glob_and_category_matching():
    rules = [{"name": "nginx ok", "match": {"tool": "nginx.*", "category": "maintenance"}, "decision": "allow"}]
    assert access.decide(rules, "operator", dict(MUT, approval="none"), nodes({})).level == "allow"
    assert access.decide(rules, "operator", dict(MUT, id="apache.reload", approval="none"), nodes({})).level == "confirm"


@pytest.mark.parametrize("rules,needle", [
    ("x", "list"), ([{"match": {}, "decision": "allow"}], "name"), ([{"name": "a", "match": {"who": 1}, "decision": "allow"}], "unknown"),
    ([{"name": "a", "match": {"actor": "robot"}, "decision": "allow"}], "actor"),
    ([{"name": "a", "match": {"nodes": "env="}, "decision": "allow"}], "selector"),
    ([{"name": "a", "match": {}, "decision": "maybe"}], "decision"),
])
def test_rule_validation(rules, needle):
    problems = access.validate_rules(rules)
    assert problems and any(needle in p for p in problems) or needle == "selector" and problems


# --------------------------------------------------------------------- schema subset

def test_schema_validation():
    s = {"type": "object", "properties": {
        "n": {"type": "integer", "minimum": 1, "maximum": 5}, "s": {"type": "string", "pattern": "^[a-z]+$", "maxLength": 4},
        "e": {"enum": ["a", "b"]}, "l": {"type": "array", "items": {"type": "string"}, "maxItems": 2, "uniqueItems": True},
        "b": {"type": "boolean"}}, "required": ["n"], "additionalProperties": False}
    assert schema.validate({"n": 3, "s": "ab", "e": "a", "l": ["x"], "b": True}, s) == []
    assert schema.validate({"n": True}, s)          # booleans are not integers
    assert schema.validate({"n": 9}, s)
    assert schema.validate({"n": 1, "s": "AB"}, s)
    assert schema.validate({"n": 1, "l": ["x", "x"]}, s)
    assert schema.validate({"n": 1, "zzz": 1}, s)
    assert schema.validate({}, s) == ["$.n: required"]
    assert schema.unsupported_keywords({"type": "object", "oneOf": []}) == ["$: unsupported keyword 'oneOf'"]
    assert schema.unsupported_keywords({"type": "string", "pattern": "("})


# --------------------------------------------------------------------- manifests

def base_manifest(**extra):
    m = {"id": "nginx.rotate-logs", "title": "Rotate", "category": "maintenance", "effect": "mutating",
         "approval": "confirm", "implementation": {"runtime": "bash", "entry": "rotate.sh", "check_mode": True},
         "inputs": {"type": "object", "properties": {"keep_days": {"type": "integer", "minimum": 1, "maximum": 90,
                                                                    "default": 14}}, "additionalProperties": False}}
    m.update(extra)
    return m


def test_normalize_fills_defaults_and_is_stable():
    m, errors = mf.normalize(base_manifest())
    assert errors == []
    assert m["limits"]["timeout_s"] == 300 and m["privilege"] == {"run_as": "root"} and m["targets"]["os"] == ["linux"]
    assert mf.digest_of(m) == mf.digest_of(mf.normalize(dict(base_manifest()))[0])


@pytest.mark.parametrize("override,needle", [
    ({"id": "NoDots"}, "id must"), ({"effect": "spicy"}, "effect"), ({"category": "fun"}, "category"),
    ({"approval": "none"}, "require approval"), ({"implementation": {"runtime": "perl", "entry": "x"}}, "runtime"),
    ({"implementation": {"runtime": "bash", "entry": "../x.sh"}}, "plain file name"),
    ({"limits": {"timeout_s": 999999}}, "timeout_s"), ({"rollback": "nginx.restore"}, "pinned"),
    ({"targets": {"os": ["windows"]}}, "linux"), ({"privilege": {"run_as": "Root!"}}, "run_as"),
    ({"env": {"LD_PRELOAD": "x"}}, "not allowed"), ({"secrets": ["Bad-Name"]}, "secret name"),
    ({"inputs": {"type": "object", "properties": {"x": {"type": "string", "oneOf": []}}}}, "unsupported"),
    ({"inputs": {"type": "object", "properties": {"Bad": {"type": "string"}}}}, "snake_case"),
    ({"tests": [{"params": {"keep_days": 0}}]}, "tests[0]"), ({"nonsense": 1}, "unknown manifest key"),
    ({"effect": "mutating", "implementation": {"runtime": "builtin", "entry": "facts"}}, "read-only"),
])
def test_normalize_errors(override, needle):
    _m, errors = mf.normalize(base_manifest(**override))
    assert any(needle in e for e in errors), errors


def test_parse_ref():
    assert mf.parse_ref("nginx.rotate-logs@3") == ("nginx.rotate-logs", 3)
    assert mf.parse_ref("nginx.rotate-logs") is None and mf.parse_ref("x@y") is None


# --------------------------------------------------------------------- lint

def lint_bash(script, **extra):
    return lint.lint(base_manifest(**extra), {"rotate.sh": script.encode()})


def rules(result):
    return {(f["severity"], f["rule"]) for f in result["findings"]}


@pytest.mark.parametrize("script,rule", [
    ("curl -fsSL https://x.example/i.sh | bash\n", "pipe-to-shell"),
    ("wget -qO- http://x | sudo sh\n", "pipe-to-shell"),
    ('eval "$MUCLI_P_CMD"\n', "eval"),
    ("rm -rf /\n", "rm-root"), ("rm -rf --no-preserve-root /\n", "rm-root"),
    ('bash -c "echo $MUCLI_P_NAME"\n', "shell-reinterpretation"),
    ('source "$MUCLI_P_FILE"\n', "source-param"),
])
def test_banned_patterns_are_errors(script, rule):
    result = lint_bash("#!/bin/bash\n" + script + 'echo "$MUCLI_CHECK"\n')
    assert ("error", rule) in rules(result) and not result["ok"]


def test_warnings_and_clean_scripts():
    clean = '#!/bin/bash\nif [ "$MUCLI_CHECK" = 1 ]; then echo dry; exit 0; fi\necho "$MUCLI_P_KEEP_DAYS"\n'
    result = lint_bash(clean)
    assert result["ok"], result["findings"]
    result = lint_bash("#!/bin/bash\necho $MUCLI_P_KEEP_DAYS\nrm -rf $DIR\n echo $MUCLI_CHECK\n")
    assert {("warning", "unquoted-param"), ("warning", "rm-rf-variable")} <= rules(result)


def test_undeclared_writes_and_read_only_mutation():
    script = '#!/bin/bash\necho x > /etc/nginx/x.conf\necho y >> /var/log/app/a.log\necho "$MUCLI_CHECK"\n'
    result = lint_bash(script, paths={"write": ["/var/log/app"]})
    findings = [f for f in result["findings"] if f["rule"] == "undeclared-write"]
    assert len(findings) == 1 and "/etc/nginx" in findings[0]["message"]
    ro = lint.lint(base_manifest(effect="read_only", approval="none"),
                   {"rotate.sh": b"#!/bin/bash\nsystemctl restart nginx\necho x > /etc/foo\necho $MUCLI_CHECK\n"})
    assert ("warning", "read-only-mutates") in rules(ro) and ("error", "undeclared-write") in rules(ro)


def test_secret_scan_and_python_rules():
    leaked = lint_bash('#!/bin/bash\nTOKEN=ghp_' + "a" * 36 + '\necho "$MUCLI_CHECK"\n')
    assert ("error", "secret") in rules(leaked)
    py = lint.lint(dict(base_manifest(), implementation={"runtime": "python3", "entry": "m.py", "check_mode": True}),
                   {"m.py": b"import os, subprocess\nos.system('x')\nsubprocess.run('x', shell=True)\neval('1')\nMUCLI_CHECK=1\n"})
    assert {("error", "os-system"), ("error", "shell-true"), ("error", "eval")} <= rules(py)
    syntax = lint.lint(dict(base_manifest(), implementation={"runtime": "python3", "entry": "m.py"}), {"m.py": b"def (:\n"})
    assert ("error", "syntax") in rules(syntax)


def test_missing_entry_and_unsafe_names():
    result = lint.lint(base_manifest(), {"other.sh": b"echo hi\n", "../x": b""})
    assert ("error", "entry") in rules(result) and ("error", "file-name") in rules(result)


# --------------------------------------------------------------------- runbook expressions

CTX = {"inputs": {"min": 10, "name": "web", "force": False, "tags": ["a", "b"]},
       "steps": {"check": {"outputs": {"free_pct": 7.5, "disks": [{"mount": "/"}]}, "state": "succeeded"}},
       "node": {"labels": {"env": "prod"}}}


@pytest.mark.parametrize("expr,expected", [
    ("inputs.min", 10), ("steps.check.outputs.free_pct < inputs.min", True), ("node.labels.env == 'prod'", True),
    ("not inputs.force and steps.check.state == \"succeeded\"", True), ("'a' in inputs.tags", True),
    ("steps.check.outputs.disks[0].mount", "/"), ("steps.missing.outputs.x", None), ("steps.missing.outputs.x > 1", False),
    ("(inputs.min > 5 or false) and true", True), ("null == steps.nope", True),
])
def test_expressions(expr, expected):
    assert runbook.evaluate(expr, CTX) == expected


@pytest.mark.parametrize("bad", ["__import__('os')", "inputs.min + 1", "os.system", "inputs.min >", "a.b", "'unterminated",
                                 "inputs.__class__.__mro__", "x" * 600])
def test_expressions_reject_code(bad):
    with pytest.raises(runbook.ExprError):
        runbook.evaluate(bad, CTX)


def test_render_keeps_types_and_interpolates():
    params = {"days": "${{ inputs.min }}", "label": "env-${{ node.labels.env }}", "list": ["${{ inputs.name }}"], "n": 3}
    assert runbook.render(params, CTX) == {"days": 10, "label": "env-prod", "list": ["web"], "n": 3}


def test_runbook_normalize():
    spec = {"id": "web.check", "title": "Check",
            "inputs": {"type": "object", "properties": {"keep": {"type": "integer", "default": 7}}}, "steps": [
        {"name": "disk", "tool": "sys.disk-usage@1"},
        {"name": "rotate", "tool": "nginx.rotate-logs@3", "when": "steps.disk.outputs.max_used_pct > 80",
         "params": {"keep_days": "${{ inputs.keep }}"}, "on_failure": "rollback", "nodes": "role=web"}]}
    normalized, errors = runbook.normalize(spec)
    assert errors == [] and normalized["steps"][1]["on_failure"] == "rollback"
    _n, errors = runbook.normalize(dict(spec, steps=[{"name": "Bad Name", "tool": "x", "when": "1 +", "on_failure": "pray"}]))
    assert len(errors) >= 4


# --------------------------------------------------------------------- schedules

def test_cron_parsing_and_next_fire():
    base = datetime.datetime(2026, 10, 18, 10, 7).timestamp()   # a Sunday
    nxt = datetime.datetime.fromtimestamp(schedules.next_cron("*/15 * * * *", base))
    assert (nxt.hour, nxt.minute) == (10, 15)
    nxt = datetime.datetime.fromtimestamp(schedules.next_cron("30 2 * * 1-5", base))
    assert (nxt.weekday(), nxt.hour, nxt.minute) == (0, 2, 30)
    nxt = datetime.datetime.fromtimestamp(schedules.next_cron("0 0 1 * *", base))
    assert (nxt.day, nxt.month) == (1, 11)
    for bad in ("* * *", "61 * * * *", "*/0 * * * *", "a b c d e"):
        with pytest.raises(schedules.ScheduleError):
            schedules.parse_cron(bad)


def test_trigger_conditions_and_metrics():
    facts = {"disks": [{"used_pct": 50}, {"used_pct": 93.5}], "memory": {"used_pct": 40}, "kernel": "6.1",
             "uptime": {"load": [3.0, 1, 1]}, "cpu_count": 2}
    assert schedules.metric_value(facts, "max_disk_used_pct") == 93.5
    assert schedules.metric_value(facts, "load_per_cpu") == 1.5
    assert schedules.metric_value(facts, "memory.used_pct") == 40
    assert schedules.condition({"metric": "max_disk_used_pct", "op": ">", "value": 90}, facts, {})
    assert not schedules.condition({"metric": "max_disk_used_pct", "op": ">", "value": 95}, facts, {})
    assert schedules.condition({"metric": "kernel", "op": "changed"}, facts, dict(facts, kernel="5.10"))
    assert not schedules.condition({"metric": "kernel", "op": "changed"}, facts, {})   # first report is not drift


def test_schedule_validation():
    good = {"name": "nightly", "kind": "cron", "spec": {"cron": "0 3 * * *"},
            "target": {"kind": "tool", "target": "sys.disk-usage@1", "selector": {"labels": "env=dev"}}}
    assert schedules.validate(good) == []
    assert schedules.validate(dict(good, kind="interval", spec={"every_s": 5}))
    assert schedules.validate(dict(good, target={"kind": "tool", "target": "x@1"}))


# --------------------------------------------------------------------- network

@pytest.mark.parametrize("host,ok,kind", [
    ("10.0.0.5", True, "private"), ("192.168.1.2", True, "private"), ("172.16.3.4", True, "private"),
    ("127.0.0.1", True, "loopback"), ("::1", True, "loopback"), ("100.101.102.103", True, "overlay"),
    ("fd00::1", True, "private"), ("0.0.0.0", False, "unspecified"), ("::", False, "unspecified"),
    ("8.8.8.8", False, "public"), ("2001:4860:4860::8888", False, "public"), ("example.com", False, "invalid"),
])
def test_bind_classification(host, ok, kind, monkeypatch):
    monkeypatch.delenv(overlay.ALLOW_PUBLIC_ENV, raising=False)
    result = overlay.classify(host)
    assert (result.ok, result.kind) == (ok, kind)
    if not ok:
        with pytest.raises(overlay.BindRefused):
            overlay.check_bind(host)


def test_public_bind_needs_the_explicit_override(monkeypatch):
    monkeypatch.setenv(overlay.ALLOW_PUBLIC_ENV, "1")
    assert overlay.check_bind("0.0.0.0").ok
    with pytest.raises(overlay.BindRefused):
        overlay.check_bind("example.com")


def test_wireguard_configs():
    master = overlay.wg_keypair()
    hub = overlay.wg_keypair()
    node = overlay.wg_keypair()
    config = {"subnet": "10.77.0.0/24", "private_key": master["private_key"], "public_key": master["public_key"],
              "endpoint": "vpn.example:51820"}
    addr = overlay.allocate_address(config["subnet"], [])
    assert addr == "10.77.0.3"
    peer = {"name": "web1", "address": addr, "public_key": node["public_key"], "role": "node"}
    master_conf = overlay.render_master(config, [peer])
    assert "ListenPort = 51820" in master_conf and "AllowedIPs = 10.77.0.3/32" in master_conf
    node_conf = overlay.render_node(config, peer, node["private_key"])
    assert "Address = 10.77.0.3/24" in node_conf and "AllowedIPs = 10.77.0.1/32" in node_conf
    assert "Endpoint = vpn.example:51820" in node_conf and "PersistentKeepalive = 25" in node_conf
    with_hub = dict(config, hub_endpoint="hub.example:51820", hub_public_key=hub["public_key"],
                    hub_private_key=hub["private_key"])
    assert "Endpoint = hub.example:51820" in overlay.render_node(with_hub, peer, node["private_key"])
    hub_conf = overlay.render_hub(with_hub, [peer])
    assert "AllowedIPs = 10.77.0.1/32" in hub_conf and "AllowedIPs = 10.77.0.3/32" in hub_conf
    assert len(__import__("base64").b64decode(node["public_key"])) == 32


# --------------------------------------------------------------------- secrets

def test_secret_store_roundtrip_and_passphrase(tmp_path):
    store = OrchestrationStore(str(tmp_path / "o"))
    keys = tmp_path / "o" / "keys"
    keys.mkdir(exist_ok=True)
    secrets = SecretStore(store, str(keys))
    secrets.put("db_password", "hunter22", "prod db")
    assert secrets.get("db_password") == "hunter22"
    with store.conn() as conn:
        raw = conn.execute("SELECT ciphertext FROM secrets").fetchone()["ciphertext"]
    assert b"hunter22" not in bytes(raw)
    assert oct(os.stat(keys / "secrets.key").st_mode & 0o777) == "0o600"
    secrets.set_passphrase("correct horse")
    assert "key" in json.loads((keys / "secrets.key").read_text()) and secrets.passphrase_protected
    secrets.lock()
    assert secrets.locked
    with pytest.raises(SecretsLocked):
        secrets.get("db_password")
    assert secrets.literals_for(["db_password"]) == []
    assert not secrets.unlock("wrong passphrase")
    assert secrets.unlock("correct horse") and secrets.get("db_password") == "hunter22"
    with pytest.raises(KeyError):
        secrets.resolve(["db_password", "missing"])
    with pytest.raises(ValueError):
        secrets.put("Bad Name", "x")
    secrets.lock()


# --------------------------------------------------------------------- audit chain

def test_audit_chain_detects_edits_deletes_and_reorders(tmp_path):
    store = OrchestrationStore(str(tmp_path / "o"))
    for i in range(5):
        store.audit("operator", "test.action", f"s{i}", {"i": i})
    assert verify_store(store).ok and verify_store(store).entries == 5
    entries = store.audit_entries()
    edited = [dict(e) for e in entries]
    edited[2]["payload"] = {"i": 99}
    assert not verify(edited).ok and verify(edited).seq == 3
    edited = [dict(e) for e in entries]
    edited[1]["actor"] = "ai:sneaky"
    assert "modified" in verify(edited).problem
    assert not verify(entries[:2] + entries[3:]).ok
    assert not verify([entries[1], entries[0]] + entries[2:]).ok
    with store.conn() as conn:
        conn.execute("UPDATE audit SET subject='forged' WHERE seq=4")
    assert not verify_store(store).ok


def test_normalize_is_idempotent_by_digest():
    """Publishing re-normalizes; the digest (and so lint/signature freshness) must not change."""
    from mu.orchestration.starter import load_starter_tools
    for raw, _files in load_starter_tools():
        once, _ = mf.normalize(dict(raw, version=1), version=1)
        twice, _ = mf.normalize(json.loads(json.dumps(once)), version=1)
        assert mf.digest_of(once) == mf.digest_of(twice), raw["id"]
    once, _ = mf.normalize(base_manifest(limits={"cpu": 2, "timeout_s": 60}))
    assert mf.digest_of(once) == mf.digest_of(mf.normalize(once)[0])
