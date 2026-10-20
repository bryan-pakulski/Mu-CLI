"""Report uptime and load. Read-only."""
import json
import os

with open("/proc/uptime", encoding="utf-8") as fh:
    uptime = float(fh.read().split()[0])
load = list(os.getloadavg())
cpus = os.cpu_count() or 1
result = {"uptime_s": uptime, "load": [round(x, 2) for x in load], "cpu_count": cpus,
          "load_per_cpu": round(load[0] / cpus, 2)}
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump(result, fh)
print(f"up {uptime / 86400:.1f} days, load {result['load']}")
