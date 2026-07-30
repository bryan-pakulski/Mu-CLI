"""Host hardware discovery and safe Docker passthrough normalization.\n\nMUCLI_CONTAINER_HARDWARE_V1\n"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any, Iterable

from .docker_cli import CommandRunner, ContainerRuntimeError
from .ref import DeviceSpec


_GPU_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def normalize_gpu_request(value: Any) -> str:
    """Return Docker's safe ``--gpus`` value, or an empty string."""
    if value is None or value is False:
        return ""
    if value is True:
        return "all"
    if isinstance(value, (list, tuple, set)):
        tokens = [str(item or "").strip() for item in value]
        tokens = [item for item in tokens if item]
        value = ",".join(tokens)
    raw = str(value or "").strip()
    if not raw or raw.lower() in {"none", "false", "off", "0"}:
        return ""
    if raw.lower() == "all":
        return "all"
    if raw.lower().startswith("device="):
        raw = raw.split("=", 1)[1].strip()
    tokens = [item.strip() for item in raw.split(",") if item.strip()]
    if not tokens:
        return ""
    if any(not _GPU_TOKEN.fullmatch(item) for item in tokens):
        raise ValueError("GPU selection may contain only indexes, UUIDs, letters, numbers, dots, colons, dashes, and underscores")
    return "device=" + ",".join(dict.fromkeys(tokens))


def normalize_device_specs(values: Iterable[DeviceSpec | dict[str, Any]] | None) -> list[DeviceSpec]:
    specs: list[DeviceSpec] = []
    seen_host: set[str] = set()
    seen_container: set[str] = set()
    for raw in values or []:
        spec = raw if isinstance(raw, DeviceSpec) else DeviceSpec.from_dict(dict(raw or {}))
        host = os.path.realpath(os.path.abspath(os.path.expanduser(spec.host_path)))
        container = str(spec.container_path or host).strip()
        if not os.path.isabs(container):
            raise ValueError(f"container device path must be absolute: {container}")
        try:
            mode = os.stat(host).st_mode
        except OSError as exc:
            raise ContainerRuntimeError(f"container device is missing or inaccessible: {host}") from exc
        if not (stat.S_ISCHR(mode) or stat.S_ISBLK(mode)):
            raise ContainerRuntimeError(f"container device is not a character or block device: {host}")
        normalized = DeviceSpec(host, container, spec.permissions)
        if normalized.host_path in seen_host:
            raise ValueError(f"host device is already attached: {normalized.host_path}")
        if normalized.container_path in seen_container:
            raise ValueError(f"container device path is already used: {normalized.container_path}")
        seen_host.add(normalized.host_path)
        seen_container.add(normalized.container_path)
        specs.append(normalized)
    return specs


def _docker_runtimes(runner: CommandRunner) -> list[str]:
    try:
        docker = runner.require("docker")
        result = runner.run([docker, "info", "--format", "{{json .Runtimes}}"], check=False)
        if result.returncode != 0:
            return []
        value = json.loads(result.stdout or "{}")
        return sorted(str(key) for key in value.keys()) if isinstance(value, dict) else []
    except Exception:
        return []


def _gpu_inventory() -> list[dict[str, Any]]:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return []
    try:
        import subprocess

        result = subprocess.run(
            [binary, "--query-gpu=index,name,uuid", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    devices: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",", 2)]
        if len(parts) != 3:
            continue
        index, name, uuid = parts
        devices.append({"id": index, "index": index, "name": name, "uuid": uuid})
    return devices


def _device_kind(path: str) -> str:
    name = os.path.basename(path)
    if path.startswith("/dev/dri/"):
        return "graphics"
    if path == "/dev/kfd":
        return "compute"
    if name.startswith("video"):
        return "camera"
    if name.startswith(("ttyUSB", "ttyACM")):
        return "serial"
    if path.startswith("/dev/snd/"):
        return "audio"
    return "device"


def discover_devices() -> list[dict[str, str]]:
    patterns = (
        "/dev/dri/renderD*",
        "/dev/dri/card*",
        "/dev/kfd",
        "/dev/video*",
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
        "/dev/snd/*",
    )
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for pattern in patterns:
        for value in sorted(glob.glob(pattern)):
            path = os.path.realpath(value)
            if path in seen:
                continue
            try:
                mode = os.stat(path).st_mode
            except OSError:
                continue
            if not (stat.S_ISCHR(mode) or stat.S_ISBLK(mode)):
                continue
            seen.add(path)
            found.append(
                {
                    "host_path": path,
                    "container_path": path,
                    "permissions": "rwm",
                    "kind": _device_kind(path),
                    "name": os.path.basename(path),
                }
            )
    return found[:80]


def detect_hardware(runner: CommandRunner | None = None) -> dict[str, Any]:
    runner = runner or CommandRunner()
    docker_available = True
    docker_error = ""
    try:
        runner.require("docker")
    except Exception as exc:
        docker_available = False
        docker_error = str(exc)

    runtimes = _docker_runtimes(runner) if docker_available else []
    inventory = _gpu_inventory()
    toolkit_detected = bool(
        "nvidia" in runtimes
        or shutil.which("nvidia-ctk")
        or shutil.which("nvidia-container-cli")
    )
    gpu_supported = bool(docker_available and inventory and toolkit_detected)
    if gpu_supported:
        reason = f"{len(inventory)} NVIDIA GPU{'s' if len(inventory) != 1 else ''} available"
    elif not docker_available:
        reason = docker_error or "Docker is unavailable"
    elif not inventory:
        reason = "No NVIDIA GPU was reported by nvidia-smi"
    else:
        reason = "NVIDIA Container Toolkit is not configured for Docker"

    return {
        "docker_available": docker_available,
        "gpu": {
            "supported": gpu_supported,
            "runtime_detected": toolkit_detected,
            "docker_runtimes": runtimes,
            "devices": inventory,
            "reason": reason,
        },
        "devices": discover_devices(),
        "warning": (
            "Hardware passthrough grants the container direct access to selected host devices. "
            "Attach only devices required by the workload."
        ),
    }


def validate_hardware(
    gpu_request: Any = None,
    devices: Iterable[DeviceSpec | dict[str, Any]] | None = None,
    *,
    runner: CommandRunner | None = None,
) -> tuple[str, list[DeviceSpec]]:
    runner = runner or CommandRunner()
    normalized_gpu = normalize_gpu_request(gpu_request)
    normalized_devices = normalize_device_specs(devices)

    if normalized_gpu:
        capability = detect_hardware(runner)
        gpu = capability.get("gpu") or {}
        if not gpu.get("supported"):
            reason = str(gpu.get("reason") or "GPU support is unavailable")
            raise ContainerRuntimeError(
                "GPU passthrough requested but unavailable: "
                f"{reason}. Install/configure NVIDIA Container Toolkit or disable GPU access."
            )
        if normalized_gpu.startswith("device="):
            requested = normalized_gpu.split("=", 1)[1].split(",")
            inventory = gpu.get("devices") or []
            available = {
                str(value)
                for item in inventory
                for value in (item.get("id"), item.get("index"), item.get("uuid"))
                if value is not None
            }
            missing = [item for item in requested if item not in available]
            if missing:
                raise ContainerRuntimeError(
                    "GPU selection is not present on this host: " + ", ".join(missing)
                )
    return normalized_gpu, normalized_devices
