"""Unprivileged Docker network isolation for MuCLI workers.

Workers are attached only to a Docker ``--internal`` bridge.  A dedicated,
capability-free proxy container is dual-homed onto that bridge and a separate
egress bridge.  HTTP and HTTPS traffic must therefore pass through the proxy,
which enforces the configured allowlist and blocklist.  The parent process
uses only ordinary Docker commands: no ``sudo``, iptables, host firewall
mutation, privileged container, or Docker socket mount is required.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .docker_cli import CommandRunner, ContainerRuntimeError, OutputCallback, run_with_output

DEFAULT_EGRESS_ALLOW = [
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "ollama.com",
    "openaipublic.blob.core.windows.net",
]
DEFAULT_PROXY_PORT = 3128


def _proxy_logs(runner: CommandRunner, docker: str, proxy_name: str, *, lines: int = 120) -> str:
    result = runner.run(
        [docker, "logs", "--tail", str(max(1, int(lines))), proxy_name],
        check=False,
    )
    return "\n".join(
        part.strip()
        for part in (result.stdout, result.stderr)
        if str(part or "").strip()
    )[-16000:]


def _wait_proxy_ready(
    runner: CommandRunner,
    docker: str,
    proxy_name: str,
    *,
    timeout: float = 30.0,
    interval: float = 0.2,
    output_callback: OutputCallback | None = None,
) -> None:
    """Wait for the unprivileged proxy process to bind its listener.

    Readiness is determined from the proxy's own startup marker instead of a
    host connection to the private Docker bridge, which also works with
    rootless Docker.  A crashed process fails immediately with its log tail.
    """
    if runner.dry_run:
        return
    deadline = time.monotonic() + max(0.1, float(timeout))
    last_logs = ""
    while time.monotonic() < deadline:
        state_result = runner.run(
            [docker, "inspect", "-f", "{{.State.Status}}|{{.State.ExitCode}}|{{.State.Error}}", proxy_name],
            check=False,
        )
        state = str(state_result.stdout or "").strip()
        last_logs = _proxy_logs(runner, docker, proxy_name)
        if "MuCLI egress proxy listening on" in last_logs:
            if output_callback is not None:
                output_callback("stdout", "MuCLI egress proxy is ready.")
            return
        if state and not state.startswith(("running|", "created|", "restarting|")):
            raise ContainerRuntimeError(
                f"egress proxy {proxy_name!r} exited before becoming ready ({state})"
                + (f"\n\nproxy log tail:\n{last_logs}" if last_logs else "")
            )
        time.sleep(max(0.05, float(interval)))
    raise ContainerRuntimeError(
        f"egress proxy {proxy_name!r} did not become ready within {timeout:.0f}s"
        + (f"\n\nproxy log tail:\n{last_logs}" if last_logs else "")
    )


def _normalise_domains(values: list[str] | None) -> list[str]:
    return list(
        dict.fromkeys(
            str(value or "").strip().lower().rstrip(".")
            for value in (values or [])
            if str(value or "").strip()
        )
    )


def proxy_name_for(network_name: str) -> str:
    base = str(network_name or "").removesuffix("-net")
    return f"{base}-proxy"[:63]


def egress_network_name_for(network_name: str) -> str:
    return f"{str(network_name or '')}-egress"[:63]


@dataclass
class NetworkPolicy:
    name: str
    network_id: str
    subnet: str
    allowed_domains: list[str]
    denied_domains: list[str] = field(default_factory=list)
    allowed_ips: list[str] = field(default_factory=list)  # legacy registry/API field
    host_allow_ports: list[int] = field(default_factory=list)
    rules: list[list[str]] = field(default_factory=list)
    host_rules: list[list[str]] = field(default_factory=list)
    proxy_name: str = ""
    proxy_ip: str = ""
    proxy_port: int = DEFAULT_PROXY_PORT
    proxy_image: str = ""
    egress_network_name: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "network_id": self.network_id,
            "subnet": self.subnet,
            "allowed_domains": self.allowed_domains,
            "denied_domains": self.denied_domains,
            "allowed_ips": self.allowed_ips,
            "host_allow_ports": self.host_allow_ports,
            "rules": self.rules,
            "host_rules": self.host_rules,
            "proxy_name": self.proxy_name,
            "proxy_ip": self.proxy_ip,
            "proxy_port": self.proxy_port,
            "proxy_image": self.proxy_image,
            "egress_network_name": self.egress_network_name,
        }


def create_isolated_network(
    name: str,
    egress_allow: list[str] | None = None,
    *,
    egress_deny: list[str] | None = None,
    host_allow: dict[str, list[int]] | None = None,
    host_allow_ports: list[int] | None = None,
    proxy_image: str | None = None,
    runner: CommandRunner | None = None,
    output_callback: OutputCallback | None = None,
) -> NetworkPolicy:
    """Create an internal worker network and an allowlist proxy.

    ``host_allow_ports`` is retained for compatibility and applies to
    ``host.docker.internal``.  New callers should pass ``host_allow`` so host
    names and ports are explicit.
    """
    runner = runner or CommandRunner()
    docker = runner.require("docker")
    network_name = str(name)
    proxy_name = proxy_name_for(network_name)
    egress_name = egress_network_name_for(network_name)
    image = str(proxy_image or "mucli/worker-proxy:latest")
    if not proxy_image and not runner.dry_run:
        raise ContainerRuntimeError("proxy_image is required for isolated networking")

    denied_domains = _normalise_domains(egress_deny)
    denied = set(denied_domains)
    allowed_domains = [
        domain
        for domain in _normalise_domains(
            egress_allow if egress_allow is not None else DEFAULT_EGRESS_ALLOW
        )
        if domain not in denied
    ]
    explicit_host_allow: dict[str, list[int]] = {}
    for raw_host, raw_ports in (host_allow or {}).items():
        host = str(raw_host or "").strip().lower().rstrip(".")
        ports = sorted(
            {
                int(port)
                for port in (raw_ports or [])
                if str(port).isdigit() and 0 < int(port) <= 65535
            }
        )
        if host and ports:
            explicit_host_allow[host] = ports
    compatibility_ports = sorted(
        {
            int(port)
            for port in (host_allow_ports or [])
            if str(port).isdigit() and 0 < int(port) <= 65535
        }
    )
    if compatibility_ports:
        explicit_host_allow.setdefault("host.docker.internal", compatibility_ports)

    created_internal = False
    created_egress = False
    created_proxy = False
    commands: list[list[str]] = []
    try:
        internal_command = [
            docker,
            "network",
            "create",
            "--driver",
            "bridge",
            "--internal",
            network_name,
        ]
        run_with_output(runner, internal_command, output_callback=output_callback)
        commands.append(internal_command)
        created_internal = True

        egress_command = [docker, "network", "create", "--driver", "bridge", egress_name]
        run_with_output(runner, egress_command, output_callback=output_callback)
        commands.append(egress_command)
        created_egress = True

        inspect = run_with_output(
            runner,
            [docker, "network", "inspect", network_name, "--format", "{{json .}}"],
            output_callback=output_callback,
        )
        if runner.dry_run:
            network_id, subnet = f"dry-{network_name}", "172.31.0.0/24"
        else:
            value = json.loads(inspect.stdout)
            network_id = str(value.get("Id") or "")
            configs = value.get("IPAM", {}).get("Config", []) or []
            subnet = str((configs[0] if configs else {}).get("Subnet") or "")
        if not subnet:
            raise ContainerRuntimeError(f"Docker network {network_name!r} has no subnet")

        proxy_command = [
            docker,
            "create",
            "--name",
            proxy_name,
            "--hostname",
            proxy_name,
            "--network",
            network_name,
            "--network-alias",
            proxy_name,
            "--restart",
            "unless-stopped",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--pids-limit",
            "256",
            "--memory",
            "256m",
            "--user",
            "65534:65534",
            "--workdir",
            "/tmp",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--label",
            "io.mucli.managed=true",
            "--label",
            "io.mucli.role=egress-proxy",
            "-e",
            f"MUCLI_PROXY_ALLOW={json.dumps(allowed_domains)}",
            "-e",
            f"MUCLI_PROXY_DENY={json.dumps(denied_domains)}",
            "-e",
            f"MUCLI_PROXY_HOST_ALLOW={json.dumps(explicit_host_allow, sort_keys=True)}",
            "-e",
            "HOME=/tmp",
            "-e",
            "MUCLI_HOME=/tmp/.mucli",
            "--entrypoint",
            "python3",
            image,
            "/opt/mucli/mu/container/egress_proxy.py",
            "--listen",
            "0.0.0.0",
            "--port",
            str(DEFAULT_PROXY_PORT),
        ]
        run_with_output(runner, proxy_command, output_callback=output_callback)
        commands.append(proxy_command)
        created_proxy = True

        connect_command = [docker, "network", "connect", egress_name, proxy_name]
        run_with_output(runner, connect_command, output_callback=output_callback)
        commands.append(connect_command)
        start_command = [docker, "start", proxy_name]
        run_with_output(runner, start_command, output_callback=output_callback)
        commands.append(start_command)
        _wait_proxy_ready(
            runner,
            docker,
            proxy_name,
            output_callback=output_callback,
        )

        proxy_inspect = run_with_output(
            runner,
            [
                docker,
                "inspect",
                "-f",
                f'{{{{(index .NetworkSettings.Networks "{network_name}").IPAddress}}}}',
                proxy_name,
            ],
            output_callback=output_callback,
        )
        proxy_ip = (
            "172.31.0.2" if runner.dry_run else str(proxy_inspect.stdout or "").strip()
        )
        if not proxy_ip:
            raise ContainerRuntimeError(
                f"egress proxy {proxy_name!r} is not attached to {network_name!r}"
            )

        return NetworkPolicy(
            name=network_name,
            network_id=network_id,
            subnet=subnet,
            allowed_domains=allowed_domains,
            denied_domains=denied_domains,
            host_allow_ports=compatibility_ports,
            rules=commands,
            proxy_name=proxy_name,
            proxy_ip=proxy_ip,
            proxy_port=DEFAULT_PROXY_PORT,
            proxy_image=image,
            egress_network_name=egress_name,
        )
    except Exception:
        if created_proxy:
            runner.run([docker, "rm", "-f", proxy_name], check=False)
        if created_egress:
            runner.run([docker, "network", "rm", egress_name], check=False)
        if created_internal:
            runner.run([docker, "network", "rm", network_name], check=False)
        raise


def teardown_network(
    name: str,
    subnet: str = "",  # retained for compatibility with older registry records
    *,
    proxy_name: str = "",
    egress_network_name: str = "",
    runner: CommandRunner | None = None,
) -> None:
    """Remove only Docker objects; never mutate the host firewall."""
    del subnet
    runner = runner or CommandRunner()
    docker = runner.require("docker")
    proxy = proxy_name or proxy_name_for(name)
    egress = egress_network_name or egress_network_name_for(name)
    runner.run([docker, "rm", "-f", proxy], check=False)
    runner.run([docker, "network", "rm", egress], check=False)
    runner.run([docker, "network", "rm", name], check=False)
