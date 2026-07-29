"""Regression tests: container trace directory bind mount.

Bug: TraceEmitter inside a container writes to the container's
``$MUCLI_HOME/trace/`` (a Docker named volume), but the host's trace
router reads ``~/.mucli/trace/``.  Traces never cross the container
boundary → "trace stats not available in container mode".

Fix: ``build_create_command`` bind-mounts the host trace directory
into the container at ``$container_volume/trace`` so JSONL files are
immediately visible on the host.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mu.container.builder import build_create_command
from mu.container.ref import ContainerRef
from utils.config import HISTORY_DIR


def _make_ref(**overrides) -> ContainerRef:
    """Build a minimal ContainerRef for testing build_create_command."""
    defaults = dict(
        container_id="abc123",
        name="mucli-test",
        image="mucli/test:abcdef123456",
        dockerfile_hash="abcdef123456",
        mounts=[],
        egress_allow=[],
        egress_deny=[],
        network_name="mucli-test-net",
        session_volume="",
        container_volume="/root/.mucli",
        worker_token="test-token",
        worker_port=30312,
        worker_protocol=1,
        supervisor_url="http://host.docker.internal:30311",
        status="building",
        attached_sessions=[],
        root_volume="mucli-test-home",
        workspace_volume="mucli-test-workspace",
        template_name="",
        standalone=True,
    )
    defaults.update(overrides)
    return ContainerRef(**defaults)


def test_build_create_command_includes_trace_mount():
    """The docker create command must bind-mount host trace dir into container."""
    ref = _make_ref()
    cmd = build_create_command(ref)
    cmd_str = " ".join(cmd)

    # The mount must target $container_volume/trace
    expected_target = f"{ref.container_volume}/trace"
    assert expected_target in cmd_str, (
        f"trace mount target {expected_target!r} not in create command"
    )

    # Must be a -v flag pair
    assert "-v" in cmd, "no -v flags in create command"

    # Find the trace mount pair
    for i, arg in enumerate(cmd):
        if arg == "-v" and i + 1 < len(cmd):
            mount_spec = cmd[i + 1]
            if expected_target in mount_spec:
                # Verify it's a rw mount
                assert ":rw" in mount_spec, f"trace mount not rw: {mount_spec}"
                # Verify host path is under HISTORY_DIR/trace
                host_path = mount_spec.rsplit(f":{expected_target}:rw", 1)[0]
                assert host_path.endswith("trace"), (
                    f"host trace path doesn't end with 'trace': {host_path}"
                )
                break
    else:
        pytest.fail("trace mount not found in create command")


def test_trace_mount_uses_absolute_history_dir():
    """Host trace path must be absolute (Docker requires absolute bind mounts)."""
    ref = _make_ref()
    cmd = build_create_command(ref)

    expected_target = f"{ref.container_volume}/trace"
    for i, arg in enumerate(cmd):
        if arg == "-v" and i + 1 < len(cmd):
            mount_spec = cmd[i + 1]
            if expected_target in mount_spec:
                host_path = mount_spec.split(":")[0]
                assert os.path.isabs(host_path), (
                    f"host trace path not absolute: {host_path}"
                )
                # Must be under the expanded HISTORY_DIR
                expected_base = os.path.abspath(os.path.expanduser(HISTORY_DIR))
                assert host_path.startswith(expected_base), (
                    f"host trace path {host_path} not under {expected_base}"
                )
                break
    else:
        pytest.fail("trace mount not found")


def test_trace_mount_created_even_without_sessions():
    """Trace mount must appear even when no sessions are attached."""
    ref = _make_ref(attached_sessions=[], standalone=True)
    cmd = build_create_command(ref)

    assert f"{ref.container_volume}/trace" in " ".join(cmd), (
        "trace mount missing when no sessions attached"
    )


def test_trace_mount_present_with_sessions():
    """Trace mount must also appear when sessions are attached."""
    ref = _make_ref(attached_sessions=["my-session"], standalone=False)
    cmd = build_create_command(ref)

    assert f"{ref.container_volume}/trace" in " ".join(cmd), (
        "trace mount missing when sessions are attached"
    )


def test_trace_mount_before_workspace_volume():
    """Trace mount should appear before workspace volume for logical grouping."""
    ref = _make_ref()
    cmd = build_create_command(ref)

    trace_idx = None
    workspace_idx = None
    for i, arg in enumerate(cmd):
        if arg == "-v" and i + 1 < len(cmd):
            mount_spec = cmd[i + 1]
            if f"{ref.container_volume}/trace" in mount_spec:
                trace_idx = i
            if "/workspace:rw" in mount_spec:
                workspace_idx = i

    assert trace_idx is not None, "trace mount not found"
    assert workspace_idx is not None, "workspace mount not found"
    assert trace_idx < workspace_idx, (
        f"trace mount (idx={trace_idx}) should come before workspace (idx={workspace_idx})"
    )


def test_trace_mount_does_not_duplicate_with_user_mounts():
    """User-provided mounts should not conflict with the trace mount."""
    from mu.container.ref import MountSpec

    ref = _make_ref(mounts=[MountSpec(host_path="/tmp/data", container_path="/data", mode="ro")])
    cmd = build_create_command(ref)

    # Trace mount still present
    trace_count = sum(
        1 for i, arg in enumerate(cmd)
        if arg == "-v"
        and i + 1 < len(cmd)
        and f"{ref.container_volume}/trace" in cmd[i + 1]
    )
    assert trace_count == 1, f"expected exactly 1 trace mount, got {trace_count}"

    # User mount still present
    assert "/tmp/data:/data:ro" in " ".join(cmd), "user mount missing"


def test_trace_dir_created_on_host():
    """build_create_command must create the host trace dir if it doesn't exist."""
    import shutil
    test_home = "/tmp/mucli-trace-test-xyz"
    shutil.rmtree(test_home, ignore_errors=True)
    with patch("mu.container.builder.HISTORY_DIR", test_home):
        ref = _make_ref()
        try:
            build_create_command(ref)
            trace_dir = os.path.join(test_home, "trace")
            assert os.path.isdir(trace_dir), f"trace dir {trace_dir} not created"
        finally:
            shutil.rmtree(test_home, ignore_errors=True)


def test_trace_emitter_writes_to_mounted_path():
    """Verify TraceEmitter.trace_dir() resolves to the mounted path inside container."""
    from mu.trace.emitter import trace_dir

    # In the container, MUCLI_HOME is not explicitly set, so it defaults
    # to ~/.mucli/ which is /root/.mucli/ — matching container_volume.
    # On the host, trace_dir() returns HISTORY_DIR/trace.
    td = trace_dir()
    assert td.endswith("trace"), f"trace_dir() doesn't end with 'trace': {td}"