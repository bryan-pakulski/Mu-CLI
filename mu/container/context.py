"""System-prompt rendering for container runtime metadata."""
from __future__ import annotations


def build_container_context(session) -> str:
    ref = getattr(session, "container_ref", None) or getattr(
        getattr(session, "session_manager", None), "container_ref", None
    )
    if ref is None:
        return ""
    mounts = getattr(ref, "mounts", []) or []
    mount_lines = [
        f"- {item.container_path} <- {item.host_path} ({item.mode})" for item in mounts
    ] or ["- /workspace (managed persistent volume)"]
    allow = ", ".join(getattr(ref, "egress_allow", []) or []) or "none"
    return (
        "LAYER 1C — Container runtime:\n"
        f"Container: {getattr(ref, 'name', 'unknown')}\n"
        f"Image: {getattr(ref, 'image', 'unknown')}\n"
        f"Status: {getattr(ref, 'status', 'unknown')}\n"
        "Mounts:\n"
        + "\n".join(mount_lines)
        + f"\nEgress allowlist: {allow}"
    )
