"""Node history (charts): agents ≥ 1.3.0 put a utilisation sample on their poll; the master keeps
it (cleaned, on its own clock), rolls old samples into 5-minute averages, and answers charts for a
node, a group or the fleet — on the web, the phone (API) and the terminal (sparklines)."""
from __future__ import annotations

import pytest

from orch_helpers import add_node, make_orch

from mu.orchestration import metrics
from mu.orchestration.commands import dispatch
from mu.orchestration.hub import Hub
from mu.orchestration.node.collectors import MetricsSampler

STAT = "cpu  {user} 0 {system} {idle} 0 0 0 0 0 0\ncpu0 1 1 1 1\n"
NET = ("Inter-|   Receive |  Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes\n"
       "    lo: 999999 0 0 0 0 0 0 0 999999 0 0 0 0 0 0 0\n"
       "  eth0: {rx} 0 0 0 0 0 0 0 {tx} 0 0 0 0 0 0 0\n"
       "docker0: 555555 0 0 0 0 0 0 0 555555 0 0 0 0 0 0 0\n")
DISK = ("   8       0 sda 100 0 {r} 0 50 0 {w} 0 0 0 0\n"
        "   8       1 sda1 90 0 {r} 0 40 0 {w} 0 0 0 0\n"
        "   7       0 loop0 1 0 999 0 1 0 999 0 0 0 0\n")


def fake_proc(state):
    def read(path):
        return {"/proc/stat": STAT.format(**state), "/proc/net/dev": NET.format(**state), "/proc/diskstats": DISK.format(**state),
                "/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 250 kB\n", "/proc/loadavg": "0.50 0.4 0.3 1/2 3"}.get(path, "")
    return read


def test_the_sampler_measures_rates_between_samples():
    state = {"user": 100, "system": 100, "idle": 800, "rx": 1000, "tx": 2000, "r": 0, "w": 0}
    clock = {"t": 100.0}
    sampler = MetricsSampler(read=fake_proc(state), clock=lambda: clock["t"], disks_fn=lambda: [{"used_pct": 40.0}, {"used_pct": 71.5}])
    first = sampler.sample()
    assert first == {"mem_pct": 75.0, "disk_pct": 71.5, "load1": 0.5}         # no rates from one sample
    state.update(user=200, system=200, idle=1000, rx=11000, tx=2000 + 5 * 1024, r=20, w=40)
    clock["t"] = 110.0
    second = sampler.sample()
    assert second["cpu_pct"] == 50.0                                          # 200 busy of 400 jiffies
    assert second["net_rx_bps"] == 1000.0 and second["net_tx_bps"] == 512.0   # lo and docker0 left out
    assert second["disk_read_bps"] == 20 * 512 / 10 and second["disk_write_bps"] == 40 * 512 / 10   # sda only, not sda1/loop


@pytest.fixture
def orch(tmp_path):
    o = make_orch(tmp_path)
    yield o
    o.stop()


def test_the_hub_keeps_clean_samples_on_the_master_clock(orch):
    node, _ = add_node(orch, "web-1")
    hub = Hub(orch)
    hub.heartbeat(node, {"agent_version": "1.3.0", "metrics": {"cpu_pct": 140, "mem_pct": "lots", "net_rx_bps": -5,
                                                                "load1": float("nan"), "evil": 1, "disk_pct": 55.5}})
    rows = orch.store.list_metrics([node.id], 0, orch.store.now() + 1, 0)
    assert len(rows) == 1
    _, ts, data = rows[0]
    assert ts == pytest.approx(orch.store.now()) and data == {"cpu_pct": 100.0, "net_rx_bps": 0.0, "disk_pct": 55.5}
    hub.heartbeat(node, {"agent_version": "1.2.0"})                           # an older agent sends none
    assert len(orch.store.list_metrics([node.id], 0, orch.store.now() + 1, 0)) == 1


def test_series_combine_nodes_and_ranges_reach_into_rollups(orch):
    a, _ = add_node(orch, "a")
    b, _ = add_node(orch, "b")
    now = orch.store.now()
    for i in range(30):                                                       # the last 10 minutes, every 20 s
        t = now - 600 + i * 20
        orch.store.add_metrics(a.id, t, {"cpu_pct": 20.0, "disk_pct": 50.0, "net_rx_bps": 100.0})
        orch.store.add_metrics(b.id, t, {"cpu_pct": 60.0, "disk_pct": 90.0, "net_rx_bps": 300.0})
    one = metrics.series(orch.store, [a.id], "1h", now=now)
    assert one["bucket_s"] == 20 and len(one["points"]) == 30 and one["points"][-1]["cpu_pct"] == 20.0
    both = metrics.series(orch.store, [a.id, b.id], "1h", now=now)
    last = both["points"][-1]
    assert (last["cpu_pct"], last["disk_pct"], last["net_rx_bps"]) == (40.0, 90.0, 400.0)   # avg, max, sum
    assert len(metrics.series(orch.store, [a.id], "30d", now=now)["points"]) <= metrics.MAX_POINTS

    # Three days on: the raw samples became 5-minute averages, still in the 7-day chart.
    later = now + 3 * 24 * 3600
    rolled = metrics.rollup(orch.store, later)
    assert rolled == 60 and orch.store.list_metrics([a.id], 0, later, 0) == []
    week = metrics.series(orch.store, [a.id, b.id], "7d", now=later)
    assert week["points"] and week["points"][0]["cpu_pct"] == 40.0
    # Past 30 days the averages go too.
    metrics.rollup(orch.store, now + 31 * 24 * 3600)
    assert metrics.series(orch.store, [a.id], "30d", now=now + 31 * 24 * 3600)["points"] == []


def test_the_api_serves_node_and_group_charts_and_names_old_agents(orch, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from mu.gui.app import create_app
    from mu.orchestration.node import AGENT_VERSION
    new, _ = add_node(orch, "new-1")
    old, _ = add_node(orch, "old-1")
    orch.store.update_node(new.id, agent_version=AGENT_VERSION)
    orch.store.update_node(old.id, agent_version="1.2.0")
    orch.store.add_metrics(new.id, orch.store.now() - 60, {"cpu_pct": 33.0})
    dispatch(orch, {"type": "putGroup", "args": {"name": "web", "members": [new.id, old.id]}}, actor="operator")
    app = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    app.state.orchestrator = orch
    http = TestClient(app)
    node = http.get(f"/api/orchestration/nodes/{new.id}/metrics?range=1h").json()
    assert node["points"][0]["cpu_pct"] == 33.0 and node["reporting"] == ["new-1"] and node["range"] == "1h"
    group = http.get("/api/orchestration/dashboard/metrics?group=web&range=6h").json()
    assert group["needs_update"] == ["old-1"] and group["nodes"] == 2 and group["scope"]["label"] == "web"
    assert http.get("/api/orchestration/dashboard/metrics?group=nope").status_code == 404
    assert http.get("/api/orchestration/nodes/nope/metrics").status_code == 404


def test_the_terminal_draws_sparklines(orch, monkeypatch, capsys):
    from mu.orchestration import cli
    from mu.commands import orch as orch_cmd
    node, _ = add_node(orch, "web-1")
    now = orch.store.now()
    for i in range(40):
        orch.store.add_metrics(node.id, now - 800 + i * 20, {"cpu_pct": float(i * 2), "net_rx_bps": 2048.0})
    monkeypatch.setattr("mu.orchestration.service.Orchestrator", lambda *a, **k: orch)
    monkeypatch.setattr(orch, "stop", lambda: None, raising=False)
    assert cli.main(["nodes", "history", "web-1", "--range", "1h"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("web-1 · last 1h") and "CPU" in out and "now 78%" in out and "2.0 kB/s" in out
    assert any(ch in out for ch in metrics.SPARK)
    monkeypatch.setattr(orch_cmd, "_orch", lambda: orch)
    lines = orch_cmd._history(orch, ["web-1", "1h"])
    assert lines[0] == "web-1 · last 1h" and any(line.startswith("CPU") for line in lines)
    with pytest.raises(ValueError):
        orch_cmd._history(orch, ["no-such-thing"])


def test_each_node_gets_its_own_small_series_for_the_health_rows(orch, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from mu.gui.app import create_app
    from mu.commands import orch as orch_cmd
    busy, _ = add_node(orch, "busy-1")
    idle, _ = add_node(orch, "idle-1")
    quiet, _ = add_node(orch, "quiet-1")                                     # no samples at all
    now = orch.store.now()
    for i in range(1100):                                                   # six hours: buckets must still be ≤ 48
        t = now - 6 * 3600 + i * 20
        orch.store.add_metrics(busy.id, t, {"cpu_pct": 90.0, "mem_pct": 70.0, "net_rx_bps": 1000.0, "net_tx_bps": 500.0})
        orch.store.add_metrics(idle.id, t, {"cpu_pct": 5.0, "mem_pct": 20.0, "disk_read_bps": 10.0, "disk_write_bps": 30.0})
    small = metrics.per_node(orch.store, [busy.id, idle.id, quiet.id], "6h", now=now)
    assert len(small[busy.id]["points"]) <= metrics.SPARK_POINTS
    assert small[busy.id]["latest"] == {"cpu_pct": 90.0, "mem_pct": 70.0, "net_bps": 1500.0, "io_bps": None}
    assert small[idle.id]["latest"]["io_bps"] == 40.0 and small[idle.id]["peak"]["cpu_pct"] == 5.0
    assert small[quiet.id] == {"points": [], "latest": {"cpu_pct": None, "mem_pct": None, "net_bps": None, "io_bps": None},
                               "peak": {"cpu_pct": None, "mem_pct": None, "net_bps": None, "io_bps": None}}

    app = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    app.state.orchestrator = orch
    body = TestClient(app).get("/api/orchestration/dashboard/metrics?per_node=1").json()
    assert set(body["per_node"]) == {busy.id, idle.id, quiet.id} and body["per_node"][busy.id]["latest"]["cpu_pct"] == 90.0
    assert "per_node" not in TestClient(app).get("/api/orchestration/dashboard/metrics").json()

    lines = orch_cmd._dashboard(orch, None)
    row = next(line for line in lines if "busy-1" in line)
    assert any(ch in row for ch in metrics.SPARK) and "90%" in row
    assert "CPU (last 6h)" in next(line for line in lines if line.strip().startswith("NODE"))
