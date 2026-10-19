"""Report memory use. Read-only: parses /proc/meminfo."""
import json
import os

info = {}
with open("/proc/meminfo", encoding="utf-8") as fh:
    for line in fh:
        key, _, rest = line.partition(":")
        fields = rest.split()
        if fields and fields[0].isdigit():
            info[key] = int(fields[0]) * 1024
total = info.get("MemTotal", 0)
available = info.get("MemAvailable", 0)
result = {"total_bytes": total, "available_bytes": available,
          "used_pct": round(100.0 * (total - available) / total, 1) if total else None,
          "swap_total_bytes": info.get("SwapTotal", 0), "swap_free_bytes": info.get("SwapFree", 0)}
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump(result, fh)
print(f"memory {result['used_pct']}% used")
