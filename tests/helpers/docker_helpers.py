"""Docker is mandatory for the test suite (documentation/testing/harness.md).

The Docker tests are the only proof of the job sandbox's isolation, the build environments,
repair containers and container test nodes, so they never skip: without Docker they fail with
a clear message. A missing or stale ``mucli-job-runtime`` image is built from this tree once per run.
"""

from __future__ import annotations

import socket
import subprocess

import pytest

_checked: dict = {}


def require_docker() -> None:
    if "docker" in _checked:
        return
    try:
        result = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.fail(f"Docker is required for the test suite and isn't available: {exc}", pytrace=False)
    if result.returncode:
        pytest.fail("Docker is required for the test suite and the daemon isn't reachable: "
                    + (result.stderr.strip() or result.stdout.strip())[:300], pytrace=False)
    _checked["docker"] = True


def require_runtime_image(tag: str = "mucli-job-runtime:latest") -> str:
    """The job runtime image, (re)built from this tree when it's missing or carries another
    tree's code fingerprint (the same staleness rule as ``mucli jobs setup``)."""
    require_docker()
    if _checked.get(tag):
        return tag
    from mu.jobs.sandbox import build
    label = build.image_digest(tag)
    if label is None or label != build.source_digest():
        try:
            build.build_image(tag)
        except Exception as exc:  # noqa: BLE001 - reported as the test failure
            pytest.fail(f"couldn't build {tag} from this tree: {exc}", pytrace=False)
    _checked[tag] = True
    return tag


def require_network(host: str, port: int = 443) -> None:
    try:
        socket.create_connection((host, port), timeout=5).close()
    except OSError as exc:
        pytest.fail(f"this Docker test needs {host}:{port} and it's unreachable: {exc}", pytrace=False)
