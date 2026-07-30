"""Actionable, transport-safe container session load failures."""
from __future__ import annotations

from typing import Any


def describe_container_load_error(
    exc: Exception,
    *,
    session_name: str = "",
    container_name: str = "",
) -> dict[str, Any]:
    technical = str(exc or "Unknown container error").strip()
    lowered = technical.lower()
    container_markers = (
        "docker", "container", "worker", "proxy", "mount", "image", "network",
    )
    is_container_failure = bool(container_name) or any(marker in lowered for marker in container_markers)
    title = "Container session could not be loaded" if is_container_failure else "Session could not be loaded"
    code = "container_load_failed" if is_container_failure else "session_load_failed"
    message = (
        "MuCLI could not start or attach the managed container."
        if is_container_failure
        else "MuCLI could not restore the saved session."
    )
    steps = (
        [
            "Open Container management and inspect the environment status and Docker output.",
            "Correct the reported configuration problem, then load the session again.",
        ]
        if is_container_failure
        else ["Inspect the technical detail below, correct the saved-session problem, and load it again."]
    )

    if any(token in lowered for token in (
        "cannot connect to the docker daemon", "docker daemon is not running",
        "is the docker daemon running", "docker: command not found", "executable file not found",
    )):
        code = "docker_unavailable"
        title = "Docker is unavailable"
        message = "MuCLI cannot reach the Docker daemon required by this session."
        steps = [
            "Start Docker and confirm `docker info` succeeds for the MuCLI user.",
            "Load the session again; its saved container configuration and volumes are retained.",
        ]
    elif any(token in lowered for token in (
        "permission denied", "docker.sock", "got permission denied while trying to connect",
    )):
        code = "docker_permission_denied"
        title = "Docker permission denied"
        message = "The MuCLI process does not have permission to use Docker."
        steps = [
            "Grant the MuCLI user access to the Docker socket, then restart the MuCLI server.",
            "Verify `docker ps` works as the same user and load the session again.",
        ]
    elif any(token in lowered for token in (
        "host mount is not a directory", "bind source path does not exist",
        "container bind mount is missing", "invalid mount config",
    )):
        code = "container_mount_missing"
        title = "A container folder mount is missing"
        message = "A host folder configured for this container no longer exists or is inaccessible."
        steps = [
            "Restore the host folder or edit/remove the mount in Container management.",
            "Load the session again after the mount path is valid.",
        ]
    elif any(token in lowered for token in (
        "template not found", "container template not found", "no such image",
        "pull access denied", "image inspect",
    )):
        code = "container_base_missing"
        title = "Container image or template is unavailable"
        message = "The saved environment cannot be rebuilt from its configured image or template."
        steps = [
            "Restore the referenced template/image or select a valid base in Container management.",
            "Repair or rebuild the environment, then load the session again.",
        ]
    elif any(token in lowered for token in (
        "missing from docker", "no such container", "managed container not found",
    )):
        code = "container_missing"
        title = "Managed container is missing"
        message = "Docker no longer has the worker container recorded for this session."
        steps = [
            "Use Container management to repair/rebuild the environment from the saved configuration.",
            "The named home and workspace volumes are preserved; load the session again after repair.",
        ]
    elif any(token in lowered for token in (
        "worker protocol", "did not become ready", "worker container has no network address",
    )):
        code = "container_worker_unavailable"
        title = "Container worker did not start"
        message = "The container exists or was rebuilt, but its MuCLI worker API is not ready."
        steps = [
            "Inspect the worker log tail shown below and correct the startup error.",
            "Restart or repair the environment, then load the session again.",
        ]
    elif any(token in lowered for token in (
        "egress proxy", "network", "proxy address changed", "not attached to",
    )):
        code = "container_network_failed"
        title = "Container network recovery failed"
        message = "MuCLI could not restore the worker and egress-proxy network topology."
        steps = [
            "Inspect Docker networks and the proxy/worker log output in Container management.",
            "Repair the environment and load the session again.",
        ]

    return {
        "code": code,
        "title": title,
        "message": message,
        "resolution_steps": steps,
        "technical_detail": technical,
        "session_name": str(session_name or ""),
        "container_name": str(container_name or ""),
        "data_preserved": True,
    }


def format_container_load_error(value: dict[str, Any]) -> str:
    lines = [str(value.get("title") or "Container session could not be loaded")]
    message = str(value.get("message") or "").strip()
    if message:
        lines.append(message)
    steps = value.get("resolution_steps") or []
    if steps:
        lines.append("Resolution:")
        lines.extend(f"  {index}. {step}" for index, step in enumerate(steps, 1))
    technical = str(value.get("technical_detail") or "").strip()
    if technical:
        lines.append(f"Technical detail: {technical}")
    return "\n".join(lines)
