"""Host-side Docker egress and host-service policy.

A normal user-defined bridge is used, then traffic from its source subnet is
filtered in Docker's supported ``DOCKER-USER`` chain.  Container-to-host traffic
uses a separate INPUT-chain policy so only the MuCLI callback port (and any
explicitly configured local provider port) is reachable from the sandbox.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from dataclasses import dataclass, field
from typing import Iterable

from .docker_cli import CommandRunner, ContainerRuntimeError, OutputCallback, run_with_output

DEFAULT_EGRESS_ALLOW = [
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "ollama.com",
]


@dataclass
class NetworkPolicy:
    name: str
    network_id: str
    subnet: str
    allowed_domains: list[str]
    denied_domains: list[str] = field(default_factory=list)
    allowed_ips: list[str] = field(default_factory=list)
    host_allow_ports: list[int] = field(default_factory=list)
    rules: list[list[str]] = field(default_factory=list)
    host_rules: list[list[str]] = field(default_factory=list)

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
        }


def resolve_domains(domains: Iterable[str]) -> dict[str, list[str]]:
    """Resolve allowlisted hostnames to IPv4 destinations.

    The resulting policy is IP based. Callers should rebuild/refresh the policy
    when providers rotate addresses; the registry preserves the original domain
    list for that purpose.
    """
    resolved: dict[str, list[str]] = {}
    for domain in domains:
        name = str(domain or "").strip().lower()
        if not name:
            continue
        try:
            ipaddress.ip_address(name)
            resolved[name] = [name]
            continue
        except ValueError:
            pass
        addresses: set[str] = set()
        try:
            for info in socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM):
                candidate = info[4][0]
                try:
                    parsed = ipaddress.ip_address(candidate)
                except ValueError:
                    continue
                if parsed.version == 4:
                    addresses.add(str(parsed))
        except socket.gaierror as exc:
            raise ContainerRuntimeError(
                f"could not resolve allowlisted domain {name}: {exc}"
            ) from exc
        if not addresses:
            raise ContainerRuntimeError(
                f"allowlisted domain resolved to no IPv4 addresses: {name}"
            )
        resolved[name] = sorted(addresses)
    return resolved


def _chain(name: str, suffix: str) -> str:
    cleaned = "".join(ch for ch in name.upper() if ch.isalnum())[:16]
    return f"MUCLI_{cleaned or 'NET'}_{suffix}"[:28]


def _iptables(runner: CommandRunner) -> str:
    geteuid = getattr(os, "geteuid", lambda: 1)
    if geteuid() == 0 or runner.dry_run:
        return runner.require("iptables")
    return runner.require("sudo")


def _run_iptables(
    runner: CommandRunner,
    args: list[str],
    *,
    output_callback: OutputCallback | None = None,
) -> None:
    binary = _iptables(runner)
    if os.path.basename(binary) == "sudo":
        run_with_output(
            runner, [binary, "iptables", *args], output_callback=output_callback
        )
    else:
        run_with_output(runner, [binary, *args], output_callback=output_callback)


def _ensure_chain_jump(
    runner: CommandRunner,
    *,
    parent_chain: str,
    chain: str,
    subnet: str,
    output_callback: OutputCallback | None = None,
) -> None:
    try:
        _run_iptables(runner, ["-N", chain], output_callback=output_callback)
    except ContainerRuntimeError:
        pass
    _run_iptables(runner, ["-F", chain], output_callback=output_callback)
    try:
        _run_iptables(
            runner,
            ["-C", parent_chain, "-s", subnet, "-j", chain],
            output_callback=output_callback,
        )
    except ContainerRuntimeError:
        _run_iptables(
            runner,
            ["-I", parent_chain, "1", "-s", subnet, "-j", chain],
            output_callback=output_callback,
        )


def create_isolated_network(
    name: str,
    egress_allow: list[str] | None = None,
    *,
    egress_deny: list[str] | None = None,
    host_allow_ports: list[int] | None = None,
    runner: CommandRunner | None = None,
    output_callback: OutputCallback | None = None,
) -> NetworkPolicy:
    """Create a bridge and apply default-deny host firewall rules.

    External forwarding is limited to resolved allowlist IPs on TCP 80/443.
    Traffic addressed to the Docker host itself is limited to explicitly
    supplied TCP ports. The container is never given ``NET_ADMIN``.
    """
    runner = runner or CommandRunner()
    docker = runner.require("docker")
    network_name = str(name)
    run_with_output(
        runner,
        [docker, "network", "create", "--driver", "bridge", network_name],
        output_callback=output_callback,
    )
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

    denied_domains = list(
        dict.fromkeys(
            str(domain or "").strip().lower()
            for domain in (egress_deny or [])
            if str(domain or "").strip()
        )
    )
    denied = set(denied_domains)
    domains = [
        domain
        for domain in dict.fromkeys(
            str(domain or "").strip().lower()
            for domain in (egress_allow or DEFAULT_EGRESS_ALLOW)
            if str(domain or "").strip()
        )
        if domain not in denied
    ]
    resolved = (
        resolve_domains(domains)
        if not runner.dry_run
        else {domain: ["203.0.113.10"] for domain in domains}
    )
    allowed_ips = sorted({ip for values in resolved.values() for ip in values})
    allowed_host_ports = sorted(
        {int(port) for port in (host_allow_ports or []) if 0 < int(port) <= 65535}
    )
    egress_chain = _chain(network_name, "OUT")
    host_chain = _chain(network_name, "HOST")

    _ensure_chain_jump(
        runner,
        parent_chain="DOCKER-USER",
        chain=egress_chain,
        subnet=subnet,
        output_callback=output_callback,
    )
    _ensure_chain_jump(
        runner,
        parent_chain="INPUT",
        chain=host_chain,
        subnet=subnet,
        output_callback=output_callback,
    )

    rules: list[list[str]] = []
    policy_rules = [
        [
            "-A",
            egress_chain,
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        ],
    ]
    for ip in allowed_ips:
        policy_rules.append(
            [
                "-A",
                egress_chain,
                "-d",
                ip,
                "-p",
                "tcp",
                "-m",
                "multiport",
                "--dports",
                "80,443",
                "-j",
                "ACCEPT",
            ]
        )
    policy_rules.append(["-A", egress_chain, "-j", "DROP"])
    for rule in policy_rules:
        _run_iptables(runner, rule, output_callback=output_callback)
        rules.append(rule)

    host_rules: list[list[str]] = [
        [
            "-A",
            host_chain,
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        ],
    ]
    if allowed_host_ports:
        host_rules.append(
            [
                "-A",
                host_chain,
                "-p",
                "tcp",
                "-m",
                "multiport",
                "--dports",
                ",".join(str(port) for port in allowed_host_ports),
                "-j",
                "ACCEPT",
            ]
        )
    host_rules.append(["-A", host_chain, "-j", "DROP"])
    for rule in host_rules:
        _run_iptables(runner, rule, output_callback=output_callback)

    return NetworkPolicy(
        name=network_name,
        network_id=network_id,
        subnet=subnet,
        allowed_domains=domains,
        denied_domains=denied_domains,
        allowed_ips=allowed_ips,
        host_allow_ports=allowed_host_ports,
        rules=rules,
        host_rules=host_rules,
    )


def _remove_chain(
    runner: CommandRunner,
    *,
    parent_chain: str,
    chain: str,
    subnet: str,
) -> None:
    if subnet:
        try:
            _run_iptables(
                runner,
                ["-D", parent_chain, "-s", subnet, "-j", chain],
            )
        except ContainerRuntimeError:
            pass
    try:
        _run_iptables(runner, ["-F", chain])
        _run_iptables(runner, ["-X", chain])
    except ContainerRuntimeError:
        pass


def teardown_network(
    name: str,
    subnet: str = "",
    *,
    runner: CommandRunner | None = None,
) -> None:
    runner = runner or CommandRunner()
    _remove_chain(
        runner,
        parent_chain="DOCKER-USER",
        chain=_chain(name, "OUT"),
        subnet=subnet,
    )
    _remove_chain(
        runner,
        parent_chain="INPUT",
        chain=_chain(name, "HOST"),
        subnet=subnet,
    )
    try:
        runner.run([runner.require("docker"), "network", "rm", name])
    except ContainerRuntimeError:
        pass
