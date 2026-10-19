"""Built-in read-only fact collectors (standard library only, never mutate anything).

``collect_facts()`` is what a node reports at enrolment and periodically on its poll. Each
collector is best-effort: a failure leaves its key out rather than failing the report.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Any, Callable, Dict, List

# Binaries whose presence a tool can require via ``targets.requires``.
KNOWN_BINARIES = (
    "systemctl", "nginx", "apache2", "httpd", "docker", "podman", "containerd", "kubectl",
    "postgres", "psql", "mysql", "redis-server", "haproxy", "caddy", "certbot", "openssl",
    "git", "rsync", "logrotate", "journalctl", "ufw", "firewall-cmd", "nft", "iptables",
    "apt", "apt-get", "dnf", "yum", "pacman", "apk", "zypper", "snap", "flatpak", "ss",
)
PACKAGE_MANAGERS = ("apt", "dnf", "yum", "pacman", "apk", "zypper")
_PSEUDO_FS = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "securityfs",
              "pstore", "bpf", "tracefs", "debugfs", "mqueue", "hugetlbfs", "configfs", "fusectl",
              "autofs", "overlay", "squashfs", "nsfs", "ramfs", "efivarfs", "binfmt_misc", "rpc_pipefs"}


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def os_release() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for line in _read("/etc/os-release").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip().lower()] = value.strip().strip('"')
    return {"id": out.get("id", ""), "version_id": out.get("version_id", ""),
            "pretty_name": out.get("pretty_name", ""), "id_like": out.get("id_like", "")}


def memory() -> Dict[str, Any]:
    info: Dict[str, int] = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            info[key] = int(parts[0]) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    return {"total_bytes": total, "available_bytes": available,
            "used_pct": round(100.0 * (total - available) / total, 1) if total else None,
            "swap_total_bytes": info.get("SwapTotal", 0)}


def disks() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] in _PSEUDO_FS or parts[1] in seen:
            continue
        mount = parts[1].replace("\\040", " ")
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total <= 0:
            continue
        free = st.f_bavail * st.f_frsize
        seen.add(parts[1])
        out.append({"mount": mount, "fs": parts[2], "total_bytes": total, "free_bytes": free,
                    "used_pct": round(100.0 * (total - free) / total, 1)})
    return out[:32]


def uptime() -> Dict[str, Any]:
    raw = _read("/proc/uptime").split()
    load = _read("/proc/loadavg").split()
    return {"uptime_s": float(raw[0]) if raw else None,
            "load": [float(x) for x in load[:3]] if len(load) >= 3 else []}


def _run(argv: List[str], timeout: float = 5.0) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"})
        return proc.stdout if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def services() -> List[str]:
    if not shutil.which("systemctl"):
        return []
    text = _run(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend",
                 "--plain", "--no-pager"])
    names = []
    for line in text.splitlines():
        unit = line.split()[0] if line.split() else ""
        if unit.endswith(".service"):
            names.append(unit[: -len(".service")])
    return sorted(names)[:256]


def binaries() -> List[str]:
    return [name for name in KNOWN_BINARIES if shutil.which(name)]


def has_systemd() -> bool:
    return os.path.isdir("/run/systemd/system")


def collect_facts() -> Dict[str, Any]:
    facts: Dict[str, Any] = {}
    collectors: Dict[str, Callable[[], Any]] = {
        "os": os_release, "memory": memory, "disks": disks, "uptime": uptime,
        "services": services, "binaries": binaries,
    }
    for key, fn in collectors.items():
        try:
            facts[key] = fn()
        except Exception:  # noqa: BLE001 - best effort, never fail a report
            continue
    facts["hostname"] = socket.gethostname()
    facts["kernel"] = platform.release()
    facts["arch"] = platform.machine()
    facts["system"] = platform.system().lower()
    facts["cpu_count"] = os.cpu_count() or 0
    facts["python"] = platform.python_version()
    facts["systemd"] = has_systemd()
    found = set(facts.get("binaries") or [])
    facts["package_managers"] = [pm for pm in PACKAGE_MANAGERS if pm in found]
    facts["container_runtimes"] = [rt for rt in ("docker", "podman", "containerd") if rt in found]
    provides = set(found) | set(facts.get("services") or [])
    if facts["systemd"]:
        provides.add("systemd")
    facts["provides"] = sorted(provides)
    facts["collected_at"] = time.time()
    return facts
