#!/usr/bin/env python3
"""Capture Environments and Insights fixtures from the real server types.

The mobile tests render against these JSON files, so the client is checked against today's response
shapes (documentation/plans/mobile_parity.md §8). Shapes come from the code the routes use:

  GET /api/containers         ContainerRef.to_dict(include_secret=False) + jobs_using/job_scan_blocks,
                              ContainerTemplate.to_dict() + jobs_using   (mu/gui/routers/containers.py)
  GET /api/containers/stats   ContainerStatsCollector rows (mu/container/stats.py _default_metric)
  GET /api/providers/pricing  utils.model_pricing.pricing_catalog()

Values are made deterministic (names, times, paths). Run from anywhere:

    python3 -I mobile/android/scripts/fixtures/capture_environments.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ANDROID = ROOT / "mobile" / "android"
sys.path.insert(0, str(ROOT))
# Importing the server writes logs under MUCLI_HOME: keep that out of the tree and the user's home.
os.environ["MUCLI_HOME"] = tempfile.mkdtemp(prefix="mucli-fixtures-")

from mu.container.ref import ContainerRef, MountSpec  # noqa: E402
from mu.container.stats import _default_metric  # noqa: E402
from mu.container.templates import ContainerTemplate  # noqa: E402
from utils import model_pricing  # noqa: E402

NOW = 1_760_000_000.0


def environments() -> dict:
    running = ContainerRef(
        container_id="c0ffee", name="mucli-research", image="mucli-env-research:latest", dockerfile_hash="abc",
        mounts=[MountSpec.from_dict({"host_path": "/home/me/dev/project", "container_path": "/workspace/project", "mode": "rw"})],
        egress_allow=["pypi.org"], network_name="mucli-net-research", worker_port=40123, created_at=NOW,
        status="running", attached_sessions=["refactor-auth", "notes", "triage"], template_name="python-base",
    )
    stopped = ContainerRef(
        container_id="beef", name="mucli-scratch", image="mucli-env-scratch:latest", dockerfile_hash="def",
        created_at=NOW, status="exited", attached_sessions=[],
    )
    blocked = ContainerRef(
        container_id="f00d", name="mucli-builder", image="mucli-env-builder:latest", dockerfile_hash="123",
        created_at=NOW, status="running", attached_sessions=["ci"],
    )
    usage = {
        "mucli-research": {"jobs_using": 2, "job_scan_blocks": []},
        "mucli-scratch": {"jobs_using": 0, "job_scan_blocks": []},
        "mucli-builder": {"jobs_using": 1, "job_scan_blocks": [{"job_id": "a1b2c3d4e5f6", "binding": "builder", "paths": ["/root/.ssh/id_ed25519"]}]},
    }
    template = ContainerTemplate(
        name="python-base", image="mucli-template-python-base:latest", source_container="mucli-research",
        description="Python 3.12 with uv", created_at=NOW, egress_allow=["pypi.org", "files.pythonhosted.org"], job_setup=["uv sync"],
    )
    listing = {
        "containers": [{**ref.to_dict(include_secret=False), **usage[ref.name]} for ref in (running, stopped, blocked)],
        "templates": [{**template.to_dict(), "jobs_using": 3}],
    }
    rows = {}
    for ref, cpu, mem in ((running, 37.5, 41.0), (stopped, 0.0, 0.0), (blocked, 91.0, 12.0)):
        row = _default_metric(ref)
        row.update({"sampled_at": NOW, "cpu_percent": cpu, "memory_percent": mem})
        if ref.status == "running":
            row.update({"memory_used_bytes": 1_610_612_736, "memory_limit_bytes": 4_294_967_296, "pids": 42, "uptime_seconds": 7_380,
                        "network_rx_bytes_per_second": 2048.0, "network_tx_bytes_per_second": 512.0, "storage_writable_bytes": 52_428_800})
        else:
            row.update({"storage_writable_bytes": 209_715_200})
        rows[ref.name] = row
    stats = {"sampled_at": NOW, "poll_after_ms": 2500, "containers": rows}
    return {"listing": listing, "stats": stats}


def pricing() -> dict:
    catalog = model_pricing.pricing_catalog()
    keep, seen = [], set()
    for item in catalog["models"]:
        if item["provider"] not in seen or len(keep) < 6:
            if item["provider"] not in seen or sum(1 for k in keep if k["provider"] == item["provider"]) < 2:
                keep.append(item)
                seen.add(item["provider"])
    catalog = {**catalog, "models": keep, "ollama": [m for m in keep if m["provider"] == "ollama"],
               "config_path": "~/.mucli/model_pricing.json", "active_config_path": "config/model_pricing.json",
               "default_config_path": "config/model_pricing.json", "using_override": False}
    return catalog


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    write(ANDROID / "__tests__" / "environments" / "fixtures" / "server.json", environments())
    write(ANDROID / "__tests__" / "insights" / "fixtures" / "pricing.json", pricing())
