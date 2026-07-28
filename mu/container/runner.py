"""Container restart and dynamic mount operations."""
from __future__ import annotations

import os
import time

from .builder import build_create_command, provider_environment
from .docker_cli import CommandRunner
from .ref import ContainerRef, MountSpec
from .registry import ContainerRegistry


def mount_folder(
    ref: ContainerRef,
    host_path: str,
    container_path: str,
    mode: str = "rw",
    *,
    preserve_changes: bool = True,
    registry: ContainerRegistry | None = None,
    runner: CommandRunner | None = None,
) -> ContainerRef:
    """Recreate a worker with an added bind mount.

    Docker cannot add a bind mount to an existing container.  When
    ``preserve_changes`` is true the writable layer is committed to a derived
    image first, so packages installed by the agent survive the recreation.
    Session data and `/workspace` are independently persisted on volumes.
    """
    runner = runner or CommandRunner()
    registry = registry or ContainerRegistry()
    docker = runner.require("docker")
    host = os.path.abspath(os.path.expanduser(host_path))
    if not os.path.isdir(host):
        raise ValueError(f"host mount is not a directory: {host}")
    mount = MountSpec(host, container_path, mode)
    if any(item.container_path == mount.container_path for item in ref.mounts):
        raise ValueError(f"container path already mounted: {mount.container_path}")

    ref.status = "restarting"
    registry.upsert(ref)
    if preserve_changes:
        snapshot_image = f"{ref.image}-mount-{int(time.time())}"
        runner.run([docker, "commit", ref.name, snapshot_image])
        ref.image = snapshot_image
    runner.run([docker, "stop", "-t", "20", ref.name], check=False)
    runner.run([docker, "rm", ref.name])
    ref.mounts.append(mount)
    create_cmd = build_create_command(ref, environment=provider_environment())
    create_cmd[0] = docker
    result = runner.run(create_cmd)
    ref.container_id = result.stdout.strip() or ref.container_id
    runner.run([docker, "start", ref.name])
    ref.status = "running"
    registry.upsert(ref)
    return ref


def attach_session_folder(
    ref: ContainerRef,
    session_name: str,
    *,
    preserve_changes: bool = True,
    registry: ContainerRegistry | None = None,
    runner: CommandRunner | None = None,
) -> ContainerRef:
    """Attach one session directory and recreate the worker if required.

    Each attached session gets only its own host session directory mounted at
    ``/root/.mucli/sessions/<name>``. This preserves many-to-one containers
    without exposing unrelated MuCLI sessions.
    """
    session_name = str(session_name or "").strip()
    if not session_name:
        raise ValueError("session name is required")
    if session_name in ref.attached_sessions:
        return ref
    runner = runner or CommandRunner()
    registry = registry or ContainerRegistry()
    docker = runner.require("docker")
    ref.status = "restarting"
    registry.upsert(ref)
    if preserve_changes:
        snapshot_image = f"{ref.image}-session-{int(time.time())}"
        runner.run([docker, "commit", ref.name, snapshot_image])
        ref.image = snapshot_image
    runner.run([docker, "stop", "-t", "20", ref.name], check=False)
    runner.run([docker, "rm", ref.name])
    ref.attached_sessions.append(session_name)
    create_cmd = build_create_command(ref, environment=provider_environment())
    create_cmd[0] = docker
    result = runner.run(create_cmd)
    ref.container_id = result.stdout.strip() or ref.container_id
    runner.run([docker, "start", ref.name])
    ref.status = "running"
    return registry.upsert(ref)

def detach_session_folder(
    ref: ContainerRef,
    session_name: str,
    *,
    preserve_changes: bool = True,
    registry: ContainerRegistry | None = None,
    runner: CommandRunner | None = None,
    recreate_if_empty: bool = False,
) -> ContainerRef:
    """Remove a session bind mount from a shared worker.

    When other sessions remain, Docker requires a recreate to actually remove
    the bind mount. If this was the final attachment the caller may simply stop
    the worker; the stale mount is then inactive and is removed on the next
    attachment/recreate.
    """
    session_name = str(session_name or "").strip()
    if session_name not in ref.attached_sessions:
        return ref
    runner = runner or CommandRunner()
    registry = registry or ContainerRegistry()
    remaining = [name for name in ref.attached_sessions if name != session_name]
    if not remaining and not recreate_if_empty:
        ref.attached_sessions = []
        return registry.upsert(ref)

    docker = runner.require("docker")
    ref.status = "restarting"
    registry.upsert(ref)
    if preserve_changes:
        snapshot_image = f"{ref.image}-detach-{int(time.time())}"
        runner.run([docker, "commit", ref.name, snapshot_image])
        ref.image = snapshot_image
    runner.run([docker, "stop", "-t", "20", ref.name], check=False)
    runner.run([docker, "rm", ref.name])
    ref.attached_sessions = remaining
    create_cmd = build_create_command(ref, environment=provider_environment())
    create_cmd[0] = docker
    result = runner.run(create_cmd)
    ref.container_id = result.stdout.strip() or ref.container_id
    runner.run([docker, "start", ref.name])
    ref.status = "running"
    return registry.upsert(ref)

