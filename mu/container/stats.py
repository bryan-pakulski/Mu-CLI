"""Live Docker container resource telemetry.

MUCLI_CONTAINER_MONITOR_V1
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from .docker_cli import CommandRunner, ContainerRuntimeError


_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?i?b|b)?\s*$", re.I)
_UNITS = {
    "": 1,
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
    "eb": 1000**6,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
    "eib": 1024**6,
}


def parse_size(value: Any) -> int:
    """Parse Docker CLI human byte values such as ``12.4MiB``."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().replace(",", "")
    match = _SIZE_RE.match(text)
    if not match:
        return 0
    number = float(match.group(1))
    unit = (match.group(2) or "").lower()
    return max(0, int(number * _UNITS.get(unit, 1)))


def parse_pair(value: Any) -> tuple[int, int]:
    left, separator, right = str(value or "").partition("/")
    if not separator:
        return parse_size(left), 0
    return parse_size(left), parse_size(right)


def parse_percent(value: Any) -> float:
    text = str(value or "0").strip().rstrip("%")
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def _iso_age_seconds(value: Any, now: float) -> float | None:
    text = str(value or "").strip()
    if not text or text.startswith("0001-"):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return max(0.0, now - parsed.timestamp())
    except ValueError:
        return None


def _safe_json_lines(value: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in str(value or "").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _gpu_request_ids(value: str) -> tuple[bool, set[str]]:
    raw = str(value or "").strip()
    if not raw:
        return False, set()
    if raw == "all":
        return True, set()
    if raw.startswith("device="):
        raw = raw.split("=", 1)[1]
    return False, {item.strip() for item in raw.split(",") if item.strip()}


def select_gpu_rows(rows: list[dict[str, Any]], request: str) -> list[dict[str, Any]]:
    all_devices, requested = _gpu_request_ids(request)
    if all_devices:
        return list(rows)
    if not requested:
        return []
    selected = []
    for row in rows:
        identities = {
            str(row.get("index") or ""),
            str(row.get("uuid") or ""),
        }
        if identities & requested:
            selected.append(row)
    return selected


def _default_metric(ref: Any) -> dict[str, Any]:
    return {
        "name": str(getattr(ref, "name", "") or ""),
        "status": str(getattr(ref, "status", "unknown") or "unknown"),
        "sampled_at": time.time(),
        "cpu_percent": 0.0,
        "memory_used_bytes": 0,
        "memory_limit_bytes": 0,
        "memory_percent": 0.0,
        "network_rx_bytes": 0,
        "network_tx_bytes": 0,
        "network_rx_bytes_per_second": 0.0,
        "network_tx_bytes_per_second": 0.0,
        "block_read_bytes": 0,
        "block_write_bytes": 0,
        "pids": 0,
        "storage_writable_bytes": 0,
        "storage_rootfs_bytes": 0,
        "restart_count": 0,
        "uptime_seconds": None,
        "gpu": {
            "requested": bool(str(getattr(ref, "gpu_request", "") or "")),
            "scope": "assigned_device_total",
            "devices": [],
            "utilization_percent": None,
            "memory_used_bytes": 0,
            "memory_total_bytes": 0,
            "temperature_c": None,
            "power_watts": None,
        },
        "attached_device_count": len(getattr(ref, "devices", []) or []),
        "error": None,
    }


class ContainerStatsCollector:
    """Collect and lightly cache resource data for all managed environments."""

    def __init__(self, runner: CommandRunner | None = None, *, min_interval: float = 0.8):
        self.runner = runner or CommandRunner()
        self.min_interval = max(0.1, float(min_interval))
        self._lock = threading.Lock()
        self._cache_at = 0.0
        self._cache: dict[str, Any] | None = None
        self._previous_network: dict[str, tuple[float, int, int]] = {}

    def collect(self, refs: Iterable[Any]) -> dict[str, Any]:
        refs = list(refs)
        now = time.time()
        with self._lock:
            if self._cache is not None and now - self._cache_at < self.min_interval:
                return copy.deepcopy(self._cache)
            payload = self._collect_locked(refs, now)
            self._cache = payload
            self._cache_at = now
            return copy.deepcopy(payload)

    def _collect_locked(self, refs: list[Any], now: float) -> dict[str, Any]:
        metrics = {str(ref.name): _default_metric(ref) for ref in refs}
        for value in metrics.values():
            value["sampled_at"] = now

        if not refs:
            return {
                "sampled_at": now,
                "poll_after_ms": 2500,
                "containers": metrics,
            }

        try:
            docker = self.runner.require("docker")
        except Exception as exc:
            for metric in metrics.values():
                metric["error"] = str(exc)
            return {
                "sampled_at": now,
                "poll_after_ms": 5000,
                "containers": metrics,
            }

        running = [
            str(ref.name)
            for ref in refs
            if str(getattr(ref, "status", "")).lower() == "running"
        ]

        if running:
            try:
                result = self.runner.run(
                    [
                        docker,
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{json .}}",
                        *running,
                    ],
                    check=False,
                    timeout=12,
                )
                if result.returncode == 0:
                    self._apply_stats(metrics, _safe_json_lines(result.stdout), now)
                else:
                    message = result.stderr.strip() or "docker stats failed"
                    for name in running:
                        metrics[name]["error"] = message
            except Exception as exc:
                for name in running:
                    metrics[name]["error"] = str(exc)

        try:
            inspect = self.runner.run(
                [docker, "inspect", "--size", *[str(ref.name) for ref in refs]],
                check=False,
                timeout=12,
            )
            if inspect.returncode == 0:
                value = json.loads(inspect.stdout or "[]")
                if isinstance(value, list):
                    self._apply_inspect(metrics, value, now)
        except Exception:
            pass

        gpu_rows = (
            self._query_gpu_rows()
            if any(
                str(getattr(ref, "gpu_request", "") or "")
                and metrics[str(ref.name)]["status"] == "running"
                for ref in refs
            )
            else []
        )
        if gpu_rows:
            for ref in refs:
                name = str(ref.name)
                if metrics[name]["status"] != "running":
                    continue
                selected = select_gpu_rows(
                    gpu_rows,
                    str(getattr(ref, "gpu_request", "") or ""),
                )
                if selected:
                    metrics[name]["gpu"] = self._gpu_summary(selected, requested=True)

        active_names = set(metrics)
        self._previous_network = {
            name: sample
            for name, sample in self._previous_network.items()
            if name in active_names
        }

        return {
            "sampled_at": now,
            "poll_after_ms": 2500,
            "containers": metrics,
        }

    def _apply_stats(
        self,
        metrics: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]],
        now: float,
    ) -> None:
        for row in rows:
            name = str(row.get("Name") or row.get("Container") or "").lstrip("/")
            metric = metrics.get(name)
            if metric is None:
                continue

            memory_used, memory_limit = parse_pair(row.get("MemUsage"))
            network_rx, network_tx = parse_pair(row.get("NetIO"))
            block_read, block_write = parse_pair(row.get("BlockIO"))
            previous = self._previous_network.get(name)
            rx_rate = tx_rate = 0.0
            if previous is not None:
                previous_at, previous_rx, previous_tx = previous
                elapsed = max(0.001, now - previous_at)
                rx_rate = max(0.0, (network_rx - previous_rx) / elapsed)
                tx_rate = max(0.0, (network_tx - previous_tx) / elapsed)
            self._previous_network[name] = (now, network_rx, network_tx)

            metric.update(
                {
                    "status": "running",
                    "cpu_percent": parse_percent(row.get("CPUPerc")),
                    "memory_used_bytes": memory_used,
                    "memory_limit_bytes": memory_limit,
                    "memory_percent": parse_percent(row.get("MemPerc")),
                    "network_rx_bytes": network_rx,
                    "network_tx_bytes": network_tx,
                    "network_rx_bytes_per_second": rx_rate,
                    "network_tx_bytes_per_second": tx_rate,
                    "block_read_bytes": block_read,
                    "block_write_bytes": block_write,
                    "pids": int(float(str(row.get("PIDs") or "0"))),
                }
            )

    @staticmethod
    def _apply_inspect(
        metrics: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]],
        now: float,
    ) -> None:
        for row in rows:
            name = str(row.get("Name") or "").lstrip("/")
            metric = metrics.get(name)
            if metric is None:
                continue
            state = row.get("State") if isinstance(row.get("State"), dict) else {}
            metric.update(
                {
                    "status": str(state.get("Status") or metric["status"]),
                    "storage_writable_bytes": int(row.get("SizeRw") or 0),
                    "storage_rootfs_bytes": int(row.get("SizeRootFs") or 0),
                    "restart_count": int(row.get("RestartCount") or 0),
                    "uptime_seconds": (
                        _iso_age_seconds(state.get("StartedAt"), now)
                        if state.get("Running")
                        else None
                    ),
                }
            )

    def _query_gpu_rows(self) -> list[dict[str, Any]]:
        try:
            nvidia_smi = self.runner.require("nvidia-smi")
            result = self.runner.run(
                [
                    nvidia_smi,
                    "--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                timeout=8,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []

        rows: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            parts = [item.strip() for item in line.split(",", 7)]
            if len(parts) != 8:
                continue
            index, uuid, name, utilization, memory_used, memory_total, temperature, power = parts
            def optional_float(value: str) -> float | None:
                try:
                    return float(value)
                except ValueError:
                    return None

            utilization_value = optional_float(utilization)
            memory_used_value = optional_float(memory_used)
            memory_total_value = optional_float(memory_total)
            if utilization_value is None or memory_used_value is None or memory_total_value is None:
                continue
            rows.append(
                {
                    "index": index,
                    "uuid": uuid,
                    "name": name,
                    "utilization_percent": utilization_value,
                    "memory_used_bytes": int(memory_used_value * 1024 * 1024),
                    "memory_total_bytes": int(memory_total_value * 1024 * 1024),
                    "temperature_c": optional_float(temperature),
                    "power_watts": optional_float(power),
                }
            )
        return rows

    @staticmethod
    def _gpu_summary(rows: list[dict[str, Any]], *, requested: bool) -> dict[str, Any]:
        memory_used = sum(int(item.get("memory_used_bytes") or 0) for item in rows)
        memory_total = sum(int(item.get("memory_total_bytes") or 0) for item in rows)
        utilization = max((float(item.get("utilization_percent") or 0.0) for item in rows), default=0.0)
        temperatures = [
            float(item["temperature_c"])
            for item in rows
            if item.get("temperature_c") is not None
        ]
        powers = [
            float(item["power_watts"])
            for item in rows
            if item.get("power_watts") is not None
        ]
        temperature = max(temperatures) if temperatures else None
        power = sum(powers) if powers else None
        return {
            "requested": requested,
            "scope": "assigned_device_total",
            "devices": rows,
            "utilization_percent": utilization,
            "memory_used_bytes": memory_used,
            "memory_total_bytes": memory_total,
            "temperature_c": temperature,
            "power_watts": power,
        }
