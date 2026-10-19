"""List failed systemd units. Read-only."""
import json
import os
import subprocess

proc = subprocess.run(["systemctl", "list-units", "--failed", "--no-legend", "--plain", "--no-pager"],
                      capture_output=True, text=True, timeout=20, check=False)
failed = []
for line in proc.stdout.splitlines():
    fields = line.split(None, 4)
    if fields:
        failed.append({"unit": fields[0], "description": fields[4] if len(fields) > 4 else ""})
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump({"failed": failed, "count": len(failed)}, fh)
print(f"{len(failed)} failed unit(s)")
