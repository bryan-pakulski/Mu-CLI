# MUCLI_CONTAINER_HARDWARE_V1
from __future__ import annotations

import os

import pytest

from mu.container.builder import build_create_command
from mu.container.hardware import normalize_device_specs, normalize_gpu_request
from mu.container.ref import ContainerRef, DeviceSpec


def _ref(**values):
    base = dict(
        container_id="",
        name="mucli-test",
        image="mucli/test:latest",
        dockerfile_hash="abc",
        network_name="mucli-test-net",
    )
    base.update(values)
    return ContainerRef(**base)


def test_gpu_request_normalization():
    assert normalize_gpu_request(None) == ""
    assert normalize_gpu_request("none") == ""
    assert normalize_gpu_request(True) == "all"
    assert normalize_gpu_request("all") == "all"
    assert normalize_gpu_request("0, 2") == "device=0,2"
    assert normalize_gpu_request(["GPU-abcd", "1"]) == "device=GPU-abcd,1"


def test_gpu_request_rejects_flag_injection():
    with pytest.raises(ValueError):
        normalize_gpu_request("all --privileged")


@pytest.mark.skipif(not os.path.exists("/dev/null"), reason="requires a POSIX device node")
def test_device_normalization_and_create_flags():
    devices = normalize_device_specs([
        {"host_path": "/dev/null", "container_path": "/dev/null", "permissions": "rwm"}
    ])
    ref = _ref(gpu_request="all", devices=devices)
    command = build_create_command(ref)

    assert command[command.index("--gpus") + 1] == "all"
    assert command[command.index("--device") + 1] == "/dev/null:/dev/null:rwm"
    assert "--privileged" not in command
    assert ["--security-opt", "no-new-privileges:true"] == command[
        command.index("--security-opt"):command.index("--security-opt") + 2
    ]


def test_container_ref_round_trip_and_legacy_defaults():
    ref = _ref(
        gpu_request="device=0",
        devices=[DeviceSpec("/dev/null", "/dev/null", "r")],
    )
    restored = ContainerRef.from_dict(ref.to_dict())
    assert restored.gpu_request == "device=0"
    assert restored.devices[0].permissions == "r"

    legacy = ContainerRef.from_dict({
        "container_id": "",
        "name": "legacy",
        "image": "legacy:latest",
        "dockerfile_hash": "x",
    })
    assert legacy.gpu_request == ""
    assert legacy.devices == []
