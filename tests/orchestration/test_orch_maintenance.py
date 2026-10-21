"""Node maintenance without custom tools: every node reports whether it needs a reboot (agent
1.2.0 facts), and the node page's Maintenance panel proposes built-in maintenance runs (reboot,
security updates, full upgrade, service control, update check) through the normal pipeline —
access policy, node policy, signing — with a dry run (check mode) beside every change."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mu.orchestration import commands, lint, maintenance
from mu.orchestration.commands import CommandRejected
from mu.orchestration.node import collectors
from mu.orchestration.starter import TOOLS_DIR, load_starter_tools

from orch_helpers import FakeNode, add_node, make_orch

ROOT = Path(__file__).resolve().parents[2]
FACTS = {"system": "linux", "systemd": True, "provides": ["systemd"], "services": ["nginx", "ssh"]}


@pytest.fixture
def orch(tmp_path):
    return make_orch(tmp_path)


def _node(orch, name, policy, facts=None):
    fake = FakeNode(orch, add_node(orch, name, {"env": "prod"})[0], policy_text=policy)
    fake.poll(facts=dict(FACTS, **(facts or {})))
    return orch.store.get_node(fake.node.id)


# --------------------------------------------------------------------- the reboot fact

def _fake_kernel(monkeypatch, tmp_path, running, installed, flag=False, pkgs=()):
    modules = tmp_path / "modules"
    for k in installed:
        (modules / k).mkdir(parents=True)
    run = tmp_path / "run"
    run.mkdir()
    if flag:
        (run / "reboot-required").write_text("*** System restart required ***\n")
        (run / "reboot-required.pkgs").write_text("".join(f"{p}\n" for p in pkgs))
    real_isdir, real_exists, real_listdir = os.path.isdir, os.path.exists, os.listdir

    def remap(path):
        path = str(path)
        if path in ("/lib/modules", "/usr/lib/modules"):
            return str(modules)
        if path.startswith("/lib/modules/"):
            return str(modules) + path[len("/lib/modules"):]
        if path.startswith("/var/run/"):
            return str(run) + path[len("/var/run"):]
        return path
    monkeypatch.setattr(collectors.os.path, "isdir", lambda p: real_isdir(remap(p)))
    monkeypatch.setattr(collectors.os.path, "exists", lambda p: real_exists(remap(p)))
    monkeypatch.setattr(collectors.os.path, "getmtime", lambda p: 1.0)
    monkeypatch.setattr(collectors.os, "listdir", lambda p: real_listdir(remap(p)))
    monkeypatch.setattr(collectors, "_read", lambda p: open(remap(p)).read() if real_exists(remap(p)) else "")
    monkeypatch.setattr(collectors.platform, "release", lambda: running)
    monkeypatch.setattr(collectors.shutil, "which", lambda name: None)


@pytest.mark.parametrize("running,installed,expect", [
    ("6.8.0-45-generic", ["6.8.0-45-generic", "6.8.0-110-generic"], "kernel 6.8.0-110-generic is installed"),
    ("6.8.0-110-generic", ["6.8.0-45-generic", "6.8.0-110-generic"], None),
    ("6.1.0-25-amd64", ["6.1.0-25-amd64", "6.1.0-26-amd64", "6.1.0-30-cloud-amd64"], "kernel 6.1.0-30-cloud-amd64 is installed"),
    ("5.14.0-427.13.1.el9_4.x86_64", ["5.14.0-427.13.1.el9_4.x86_64", "5.14.0-503.11.1.el9_5.x86_64"], "kernel 5.14.0-503.11.1.el9_5.x86_64"),
    ("6.18.56-1-lts", ["6.18.57-1-lts"], "running kernel 6.18.56-1-lts was removed"),
])
def test_reboot_detection_from_installed_kernels(monkeypatch, tmp_path, running, installed, expect):
    _fake_kernel(monkeypatch, tmp_path, running, installed)
    status = collectors.reboot_status()
    assert status["required"] == bool(expect)
    if expect:
        assert any(expect in r for r in status["reasons"]), status
    assert status["running_kernel"] == running


def test_reboot_detection_from_the_package_manager_flag(monkeypatch, tmp_path):
    _fake_kernel(monkeypatch, tmp_path, "6.8.0-45-generic", ["6.8.0-45-generic"], flag=True, pkgs=["libc6", "openssl"])
    status = collectors.reboot_status()
    assert status["required"] and status["packages"] == ["libc6", "openssl"]
    assert "package manager requested a reboot" in status["reasons"][0]
    assert '"reboot": reboot_status' in Path(collectors.__file__).read_text()    # part of every facts report


NEEDS = ("Core libraries or services have been updated since boot-up:\n  * kernel\n  * glibc\n\n"
         "Reboot is required to fully utilize these updates.\nMore information: https://access.redhat.com/solutions/27943\n")
FINE = "No core libraries or services have been updated since boot-up.\nReboot should not be necessary.\n"


@pytest.mark.parametrize("code,stdout,stderr,required,error", [
    (1, NEEDS, "", True, ""),
    (0, FINE, "", False, ""),
    # It exits 1 on failure too (the agent runs unprivileged): that is not a reboot.
    (1, "", "Error: This command has to be run with superuser privileges (under the root user on most systems).", False, "superuser"),
    (1, "", "Error: Failed to obtain the transaction lock (logged in as: mucli-node).", False, "transaction lock"),
])
def test_needs_restarting_only_counts_when_it_says_reboot_is_required(monkeypatch, tmp_path, code, stdout, stderr, required, error):
    """Amazon Linux / RHEL (agent 1.3.1): a failed `needs-restarting -r` used to read as a reboot
    being needed, so the warning outlived every reboot."""
    from types import SimpleNamespace
    _fake_kernel(monkeypatch, tmp_path, "6.1.174-217.345.amzn2023.x86_64", ["6.1.174-217.345.amzn2023.x86_64"])
    monkeypatch.setattr(collectors.shutil, "which", lambda name: "/usr/bin/needs-restarting" if name == "needs-restarting" else None)
    monkeypatch.setattr(collectors.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr))
    status = collectors.reboot_status()
    assert status["required"] is required
    if required:
        assert status["packages"] == ["glibc", "kernel"] and "needs-restarting" in status["reasons"][0]
    assert (error in status["errors"][0]) if error else status["errors"] == []


# --------------------------------------------------------------------- the built-in tools

def test_maintenance_tools_ship_lint_clean_with_check_mode():
    tools = {m["id"]: (m, f) for m, f in load_starter_tools()}
    for tool_id in ("os.reboot", "os.security-updates", "os.full-upgrade", "os.service"):
        manifest, files = tools[tool_id]
        assert manifest["effect"] == "mutating" and manifest["implementation"]["check_mode"]
        result = lint.lint(manifest, files)
        assert result["ok"], (tool_id, result.get("findings"))
    assert tools["os.reboot"][0]["idempotent"] is False           # never silently re-dispatched


def test_an_existing_master_gains_new_built_ins(orch):
    orch.install_starter_pack(only=["sys.pending-updates"])     # an older pack
    orch.store.set_setting("starter_installed", True)
    added = orch.install_starter_pack()
    assert "os.reboot@1" in added and "sys.pending-updates@1" not in added
    assert orch.install_starter_pack() == []                      # repeatable, nothing twice


def _script(tool_dir, entry, params, tmp_path, *, check, path_dirs=()):
    result = tmp_path / "result.json"
    (tmp_path / "params.json").write_text(json.dumps(params))
    env = {"PATH": os.pathsep.join([*path_dirs, "/usr/bin", "/bin"]), "MUCLI_CHECK": "1" if check else "0",
           "MUCLI_PARAMS": str(tmp_path / "params.json"), "MUCLI_RESULT": str(result)}
    proc = subprocess.run([sys.executable, str(Path(TOOLS_DIR) / tool_dir / entry)], env=env, capture_output=True, text=True, timeout=60)
    return proc, json.loads(result.read_text())


def _fake_bin(tmp_path, name, log):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text(f"#!/bin/sh\necho \"{name} $@\" >> {log}\nexit 0\n")
    script.chmod(0o755)
    return bindir


def test_reboot_check_mode_never_schedules_and_real_mode_uses_a_timer(tmp_path):
    log = tmp_path / "calls.log"
    bindir = _fake_bin(tmp_path, "systemd-run", log)
    proc, out = _script("reboot", "reboot.py", {"delay_s": 45}, tmp_path, check=True, path_dirs=[str(bindir)])
    assert proc.returncode == 0 and out["check_mode"] and not out["scheduled"] and not log.exists()
    proc, out = _script("reboot", "reboot.py", {"delay_s": 45}, tmp_path, check=False, path_dirs=[str(bindir)])
    assert proc.returncode == 0 and out["scheduled"] and out["delay_s"] == 45
    call = log.read_text()
    assert "--on-active=45s" in call and "systemctl reboot" in call
    log.unlink()
    proc, out = _script("reboot", "reboot.py", {"only_if_required": True}, tmp_path, check=False, path_dirs=[str(bindir)])
    if not out["required"]:                                       # this machine needs none: nothing scheduled
        assert not out["scheduled"] and not log.exists()


# --------------------------------------------------------------------- the panel and the command

def test_panel_says_what_each_node_allows(orch):
    orch.install_starter_pack()
    strict = _node(orch, "db-1", "max_effect: read_only\n", {"reboot": {"required": False, "reasons": []}})
    open_ = _node(orch, "web-1", "max_effect: mutating\n",
                  {"reboot": {"required": True, "reasons": ["kernel 6.8.0-47 is installed; 6.8.0-45 is running"]}})
    panel = maintenance.node_panel(orch.store, strict)
    by = {a["key"]: a for a in panel["actions"]}
    assert by["check_updates"]["available"]
    assert not by["reboot"]["available"] and "max_effect: mutating" in by["reboot"]["reason"]
    assert by["reboot"]["check_available"]                       # a dry run is read-only
    assert panel["reboot"]["required"] is False and panel["services"] == ["nginx", "ssh"]
    panel = maintenance.node_panel(orch.store, open_)
    assert all(a["available"] for a in panel["actions"]) and panel["reboot"]["required"]
    summary = maintenance.fleet_reboot_summary(orch.store)
    assert summary["count"] == 1 and summary["nodes"][0]["name"] == "web-1"
    from mu.orchestration import vm
    assert vm.overview(orch)["reboot"]["count"] == 1
    assert vm.node_vm(orch.store, open_)["summary"]["reboot_required"] is True
    old = _node(orch, "old-1", "max_effect: mutating\n")             # an agent before 1.2.0
    assert maintenance.node_panel(orch.store, old)["reboot"]["required"] is None


def test_maintenance_runs_go_through_approval_and_reboots_roll(orch):
    orch.install_starter_pack()
    a = _node(orch, "web-1", "max_effect: mutating\n")
    b = _node(orch, "web-2", "max_effect: mutating\n")
    run = commands.dispatch(orch, {"type": "nodeMaintenance", "args": {
        "node_ids": [a.id, b.id], "action": "reboot", "params": {"only_if_required": True}}}, actor="operator").data["run"]
    assert run["state"] == "proposed" and run["target_id"] == "os.reboot"           # mutating → confirm
    assert run["strategy"]["batch"] == 1 and run["strategy"]["max_parallel"] == 1  # one node at a time
    assert run["params"]["only_if_required"] is True and run["title"] == "Reboot on 2 nodes"
    svc = commands.dispatch(orch, {"type": "nodeMaintenance", "args": {
        "node_id": a.id, "action": "service", "params": {"unit": "nginx", "action": "reload"}, "check_mode": True}},
        actor="operator").data["run"]
    assert svc["check_mode"] and svc["title"] == "Service nginx (reload) on web-1 (check)"
    with pytest.raises(CommandRejected):
        commands.dispatch(orch, {"type": "nodeMaintenance", "args": {"node_id": a.id, "action": "reboot"}}, actor="ai:chat")
    with pytest.raises(commands.CommandError):
        commands.dispatch(orch, {"type": "nodeMaintenance", "args": {"node_id": a.id, "action": "service"}}, actor="operator")
    with pytest.raises(commands.CommandError):
        commands.dispatch(orch, {"type": "nodeMaintenance", "args": {"node_id": a.id, "action": "format_disk"}}, actor="operator")

