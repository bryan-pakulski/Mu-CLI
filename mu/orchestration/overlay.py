"""Private networking (plan §6.0): bind checks and WireGuard overlay configs.

Nothing listens on a public interface. ``check_bind`` refuses the unspecified address
(``0.0.0.0``/``::``) and any globally routable address unless ``MUCLI_ORCH_ALLOW_PUBLIC_BIND=1``
documents an explicit override. The overlay helper generates WireGuard keys and ``wg-quick``
configs for the master, an optional hub (a relay VPS running only WireGuard) and each node;
WireGuard itself comes from the distribution's ``wireguard-tools``.
"""

from __future__ import annotations

import base64
import ipaddress
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

DEFAULT_SUBNET = "10.77.0.0/24"
DEFAULT_WG_PORT = 51820
DEFAULT_NODE_PORT = 30312
ALLOW_PUBLIC_ENV = "MUCLI_ORCH_ALLOW_PUBLIC_BIND"


class BindRefused(ValueError):
    pass


@dataclass(frozen=True)
class BindCheck:
    ok: bool
    kind: str      # loopback | private | overlay | public | unspecified | invalid
    reason: str = ""


def classify(host: str) -> BindCheck:
    try:
        ip = ipaddress.ip_address(str(host or "").split("%")[0])
    except ValueError:
        return BindCheck(False, "invalid", "the node endpoint must bind to an IP address, not a host name")
    if ip.is_unspecified:
        return BindCheck(False, "unspecified", f"{host} listens on every interface, including public ones")
    if ip.is_loopback:
        return BindCheck(True, "loopback", "reachable only from this machine (SSH tunnel layout)")
    if ip in ipaddress.ip_network("100.64.0.0/10"):
        return BindCheck(True, "overlay", "carrier-grade NAT range (Tailscale/Headscale overlay)")
    if ip.is_private or ip.is_link_local:
        return BindCheck(True, "private", "private network address")
    return BindCheck(False, "public", f"{host} is a public address")


def check_bind(host: str, *, allow_public: Optional[bool] = None) -> BindCheck:
    """Raise ``BindRefused`` unless ``host`` is loopback, private or an overlay address."""
    result = classify(host)
    if result.ok:
        return result
    override = os.environ.get(ALLOW_PUBLIC_ENV) == "1" if allow_public is None else allow_public
    if override and result.kind in ("public", "unspecified"):
        return BindCheck(True, result.kind, f"{result.reason} (explicitly allowed by {ALLOW_PUBLIC_ENV}=1)")
    raise BindRefused(f"refusing to start the node endpoint on {host}: {result.reason}. "
                      "Bind it to a private, overlay or loopback address.")


def local_addresses() -> List[Dict[str, str]]:
    """Every address this machine has, per interface (Linux: ``/proc`` and ``ioctl``; elsewhere
    the host name's addresses)."""
    out: List[Dict[str, str]] = []
    seen = set()

    def add(iface: str, address: str) -> None:
        if (iface, address) not in seen:
            seen.add((iface, address))
            out.append({"interface": iface, "address": address})

    try:
        import fcntl
        import socket
        import struct
        for _idx, iface in socket.if_nameindex():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                packed = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", iface.encode()[:15]))  # SIOCGIFADDR
                add(iface, socket.inet_ntoa(packed[20:24]))
            except OSError:
                pass
            finally:
                sock.close()
    except (ImportError, OSError, AttributeError):
        pass
    try:
        with open("/proc/net/if_inet6", "r", encoding="ascii") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 6:
                    raw = parts[0]
                    add(parts[5], str(ipaddress.ip_address(":".join(raw[i:i + 4] for i in range(0, 32, 4)))))
    except OSError:
        pass
    if not out:
        import socket
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                add("", str(info[4][0]))
        except OSError:
            pass
        add("lo", "127.0.0.1")
    return out


def interfaces(addresses: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    """Local addresses with the endpoint's classification: which can be bound and why not."""
    rows = []
    for item in local_addresses() if addresses is None else addresses:
        check = classify(item["address"])
        rows.append({"interface": item.get("interface", ""), "address": item["address"], "kind": check.kind,
                     "ok": check.ok, "reason": check.reason,
                     "family": "ipv6" if ":" in item["address"] else "ipv4"})
    order = {"overlay": 0, "private": 1, "loopback": 2}
    rows.sort(key=lambda r: (not r["ok"], order.get(r["kind"], 9), r["family"], r["address"]))
    return rows


# --------------------------------------------------------------------- WireGuard

def wg_keypair() -> Dict[str, str]:
    key = X25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"private_key": base64.b64encode(private).decode(), "public_key": base64.b64encode(public).decode()}


def allocate_address(subnet: str, used: List[str], *, reserve: int = 2) -> str:
    """Next free host address (``.1`` master, ``.2`` hub are reserved by default)."""
    net = ipaddress.ip_network(subnet, strict=False)
    taken = {str(ipaddress.ip_interface(u).ip) if "/" in u else u for u in used}
    for i, host in enumerate(net.hosts()):
        if i < reserve:
            continue
        if str(host) not in taken:
            return str(host)
    raise ValueError(f"no free addresses left in {subnet}")


def master_address(subnet: str) -> str:
    return str(next(ipaddress.ip_network(subnet, strict=False).hosts()))


def hub_address(subnet: str) -> str:
    hosts = ipaddress.ip_network(subnet, strict=False).hosts()
    next(hosts)
    return str(next(hosts))


def _peer(public_key: str, allowed: str, endpoint: str = "", keepalive: bool = False, comment: str = "") -> str:
    lines = ["", "[Peer]"]
    if comment:
        lines.insert(1, f"# {comment}")
    lines += [f"PublicKey = {public_key}", f"AllowedIPs = {allowed}"]
    if endpoint:
        lines.append(f"Endpoint = {endpoint}")
    if keepalive:
        lines.append("PersistentKeepalive = 25")
    return "\n".join(lines)


def interface(private_key: str, address: str, prefix: int, listen_port: Optional[int] = None) -> str:
    lines = ["[Interface]", f"PrivateKey = {private_key}", f"Address = {address}/{prefix}"]
    if listen_port:
        lines.append(f"ListenPort = {listen_port}")
    return "\n".join(lines)


def render_master(config: Dict[str, Any], peers: List[Dict[str, Any]]) -> str:
    """The master's ``wg-mucli.conf``. With a hub the master has one peer (the hub)."""
    subnet = config["subnet"]
    prefix = ipaddress.ip_network(subnet, strict=False).prefixlen
    if config.get("hub_endpoint"):
        text = interface(config["private_key"], master_address(subnet), prefix)
        text += _peer(config["hub_public_key"], subnet, config["hub_endpoint"], keepalive=True, comment="hub")
        return text + "\n"
    text = interface(config["private_key"], master_address(subnet), prefix, config.get("listen_port") or DEFAULT_WG_PORT)
    for peer in peers:
        if peer.get("role") == "node":
            text += _peer(peer["public_key"], f"{peer['address']}/32", comment=peer["name"])
    return text + "\n"


def render_hub(config: Dict[str, Any], peers: List[Dict[str, Any]]) -> str:
    """The hub relays between master and nodes; it runs no TCP services at all."""
    subnet = config["subnet"]
    prefix = ipaddress.ip_network(subnet, strict=False).prefixlen
    text = interface(config["hub_private_key"], hub_address(subnet), prefix,
                     config.get("listen_port") or DEFAULT_WG_PORT)
    text += "\n# The hub forwards overlay traffic only. Enable with: sysctl -w net.ipv4.ip_forward=1"
    text += _peer(config["public_key"], f"{master_address(subnet)}/32", comment="master")
    for peer in peers:
        if peer.get("role") == "node":
            text += _peer(peer["public_key"], f"{peer['address']}/32", comment=peer["name"])
    return text + "\n"


def render_node(config: Dict[str, Any], peer: Dict[str, Any], node_private_key: str) -> str:
    """A node's ``wg-mucli.conf``: it reaches only the master (directly or via the hub)."""
    subnet = config["subnet"]
    prefix = ipaddress.ip_network(subnet, strict=False).prefixlen
    text = interface(node_private_key, peer["address"], prefix)
    if config.get("hub_endpoint"):
        text += _peer(config["hub_public_key"], f"{master_address(subnet)}/32", config["hub_endpoint"],
                      keepalive=True, comment="hub (routes to the master)")
    else:
        text += _peer(config["public_key"], f"{master_address(subnet)}/32", config.get("endpoint") or "",
                      keepalive=True, comment="master")
    return text + "\n"
