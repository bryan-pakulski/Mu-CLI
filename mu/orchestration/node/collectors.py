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


def _version_key(text: str) -> List[Any]:
    """Natural sort key for kernel versions (``6.8.0-45-generic`` < ``6.8.0-110-generic``)."""
    import re
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def reboot_status() -> Dict[str, Any]:
    """Whether this machine needs a reboot, and why (read-only; best effort).

    * Debian/Ubuntu: ``/var/run/reboot-required`` (and ``.pkgs`` for the packages behind it).
    * RHEL/Fedora: ``needs-restarting -r`` exits 1 when a reboot is needed.
    * Any distro: a newer kernel is installed (``/lib/modules``) than the one running, or the
      running kernel's modules are gone (Arch removes them on upgrade).
    """
    reasons: List[str] = []
    packages: List[str] = []
    since = None
    flag = "/var/run/reboot-required"
    if os.path.exists(flag):
        reasons.append("the package manager requested a reboot")
        try:
            since = os.path.getmtime(flag)
        except OSError:
            pass
        packages = sorted({line.strip() for line in _read(flag + ".pkgs").splitlines() if line.strip()})[:100]
    errors: List[str] = []
    if shutil.which("needs-restarting"):
        try:
            proc = subprocess.run(["needs-restarting", "-r"], capture_output=True, text=True, timeout=20,
                                  env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"})
            verdict = needs_restarting_verdict(proc.returncode, proc.stdout, proc.stderr)
            if verdict["required"]:
                reasons.append("needs-restarting reports core libraries or the kernel were updated")
                packages = sorted(set(packages) | set(verdict["packages"]))[:100]
            elif verdict["error"]:
                errors.append(verdict["error"])
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"needs-restarting didn't run: {exc}"[:200])
    running = platform.release()
    modules = "/lib/modules" if os.path.isdir("/lib/modules") else "/usr/lib/modules"
    try:
        installed = [d for d in os.listdir(modules) if os.path.isdir(os.path.join(modules, d))]
    except OSError:
        installed = []
    if installed and running:
        flavour = running.split("-")[-1] if "-" in running else ""
        if "." in flavour:                     # RHEL: 5.14.0-427.13.1.el9_4.x86_64 → match the arch
            flavour = flavour.rsplit(".", 1)[-1]
        same_flavour = [k for k in installed if not flavour or k.endswith(flavour)] or installed
        newest = max(same_flavour, key=_version_key)
        if running not in installed:
            reasons.append(f"the running kernel {running} was removed; {newest} is installed")
        elif _version_key(newest) > _version_key(running):
            reasons.append(f"kernel {newest} is installed; {running} is running")
    return {"required": bool(reasons), "reasons": reasons, "packages": packages, "since": since,
            "running_kernel": running, "errors": errors}


def needs_restarting_verdict(code: int, stdout: str, stderr: str) -> Dict[str, Any]:
    """Read ``needs-restarting -r``. It exits 1 both when a reboot is needed *and* when it fails
    (an unprivileged user, a locked or unreadable cache…), so only its own words count: "Reboot is
    required" (with the packages it lists as "* name"), or "Reboot should not be necessary"."""
    text = stdout or ""
    if code == 1 and "reboot is required" in text.lower():
        names = [line.strip()[1:].strip().split()[0] for line in text.splitlines() if line.strip().startswith("*")]
        return {"required": True, "packages": [n for n in names if n], "error": ""}
    if code == 0 or "should not be necessary" in text.lower():
        return {"required": False, "packages": [], "error": ""}
    detail = " ".join((stderr or text or f"exit {code}").split())[:160]
    return {"required": False, "packages": [], "error": f"needs-restarting failed: {detail}"}


def collect_facts() -> Dict[str, Any]:
    facts: Dict[str, Any] = {}
    collectors: Dict[str, Callable[[], Any]] = {
        "os": os_release, "memory": memory, "disks": disks, "uptime": uptime,
        "services": services, "binaries": binaries, "reboot": reboot_status,
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


# ------------------------------------------------------------------ metrics (history charts)
#
# A small sample rides on the node's poll every METRICS_INTERVAL_S: utilisation the master keeps
# as history (node page charts). Rates (CPU, network, disk I/O) come from the difference between
# this sample's kernel counters and the previous one's, so the first sample has none of them.
# Linux /proc only; elsewhere the keys are left out.

def _cpu_times(read: Callable[[str], str]) -> Any:
    for line in read("/proc/stat").splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:] if v.isdigit()]
            if len(values) >= 4:
                idle = values[3] + (values[4] if len(values) > 4 else 0)       # idle + iowait
                return sum(values[:8]), idle
    return None


def _net_bytes(read: Callable[[str], str]) -> Any:
    rx = tx = 0
    seen = False
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, sep, rest = line.partition(":")
        name = name.strip()
        if not sep or name == "lo" or name.startswith(("veth", "docker", "br-", "virbr")):
            continue
        fields = rest.split()
        if len(fields) >= 9:
            rx += int(fields[0])
            tx += int(fields[8])
            seen = True
    return (rx, tx) if seen else None


def _disk_bytes(read: Callable[[str], str]) -> Any:
    """Bytes read and written by whole block devices (partitions, loop and ram devices skipped)."""
    rows = [line.split() for line in read("/proc/diskstats").splitlines()]
    names = {parts[2] for parts in rows if len(parts) > 2}
    rd = wr = 0
    seen = False
    for parts in rows:
        if len(parts) < 10:
            continue
        name = parts[2]
        if name.startswith(("loop", "ram", "zram", "dm-", "sr")):
            continue
        # A partition (sda1, nvme0n1p1, mmcblk0p1) whose parent device is listed: counted there.
        parent = name.rstrip("0123456789")
        if parent != name and (parent in names or parent.rstrip("p") in names):
            continue
        rd += int(parts[5]) * 512
        wr += int(parts[9]) * 512
        seen = True
    return (rd, wr) if seen else None


class MetricsSampler:
    """Successive samples: {cpu_pct, mem_pct, disk_pct, load1, net_rx_bps, net_tx_bps,
    disk_read_bps, disk_write_bps}; a key is absent when it can't be measured (yet)."""

    def __init__(self, read: Callable[[str], str] = _read, clock: Callable[[], float] = time.monotonic,
                 disks_fn: Callable[[], List[Dict[str, Any]]] = disks):
        self._read = read
        self._clock = clock
        self._disks = disks_fn
        self._prev: Dict[str, Any] = {}

    def sample(self) -> Dict[str, Any]:
        read = self._read
        now = self._clock()
        out: Dict[str, Any] = {}
        prev, cur = self._prev, {"t": now, "cpu": _cpu_times(read), "net": _net_bytes(read), "io": _disk_bytes(read)}
        dt = now - prev["t"] if prev else 0.0
        if prev and cur["cpu"] and prev.get("cpu"):
            total = cur["cpu"][0] - prev["cpu"][0]
            idle = cur["cpu"][1] - prev["cpu"][1]
            if total > 0:
                out["cpu_pct"] = round(max(0.0, min(100.0, 100.0 * (total - idle) / total)), 1)
        if prev and dt > 0:
            for key, names in (("net", ("net_rx_bps", "net_tx_bps")), ("io", ("disk_read_bps", "disk_write_bps"))):
                if cur[key] and prev.get(key):
                    for i, name in enumerate(names):
                        delta = cur[key][i] - prev[key][i]
                        if delta >= 0:                      # a counter reset skips one point
                            out[name] = round(delta / dt, 1)
        self._prev = cur
        info: Dict[str, int] = {}
        for line in read("/proc/meminfo").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                info[key] = int(parts[0])
        if info.get("MemTotal"):
            out["mem_pct"] = round(100.0 * (info["MemTotal"] - info.get("MemAvailable", 0)) / info["MemTotal"], 1)
        try:
            used = [d["used_pct"] for d in self._disks() if d.get("used_pct") is not None]
            if used:
                out["disk_pct"] = max(used)
        except Exception:  # noqa: BLE001 - best effort
            pass
        load = read("/proc/loadavg").split()
        if load:
            try:
                out["load1"] = float(load[0])
            except ValueError:
                pass
        return out
