"""Report filesystem usage. Read-only: only statvfs and /proc/mounts."""
import json
import os

PSEUDO = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "overlay", "squashfs",
          "securityfs", "pstore", "bpf", "tracefs", "debugfs", "mqueue", "hugetlbfs", "configfs",
          "fusectl", "autofs", "nsfs", "ramfs", "efivarfs", "binfmt_misc", "rpc_pipefs"}

with open(os.environ["MUCLI_PARAMS"], encoding="utf-8") as fh:
    params = json.load(fh)
minimum = params.get("min_used_pct", 0)
disks, seen = [], set()
with open("/proc/mounts", encoding="utf-8") as fh:
    for line in fh:
        parts = line.split()
        if len(parts) < 3 or parts[2] in PSEUDO or parts[1] in seen:
            continue
        try:
            st = os.statvfs(parts[1])
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total <= 0:
            continue
        free = st.f_bavail * st.f_frsize
        seen.add(parts[1])
        used = round(100.0 * (total - free) / total, 1)
        if used >= minimum:
            disks.append({"mount": parts[1], "fs": parts[2], "total_bytes": total, "free_bytes": free,
                          "used_pct": used})
result = {"disks": disks, "max_used_pct": max((d["used_pct"] for d in disks), default=None)}
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump(result, fh)
for d in disks:
    print(f"{d['mount']:<30} {d['used_pct']:>5}% used")
