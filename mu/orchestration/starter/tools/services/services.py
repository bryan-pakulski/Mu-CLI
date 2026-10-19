"""List systemd services. Read-only: systemctl list-units."""
import json
import os
import subprocess

with open(os.environ["MUCLI_PARAMS"], encoding="utf-8") as fh:
    params = json.load(fh)
argv = ["systemctl", "list-units", "--type=service", "--no-legend", "--plain", "--no-pager", "--all"]
if params.get("state", "running") != "all":
    argv.append("--state=" + params.get("state", "running"))
proc = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
services = []
for line in proc.stdout.splitlines():
    fields = line.split(None, 4)
    if len(fields) >= 4 and fields[0].endswith(".service"):
        services.append({"name": fields[0][: -len(".service")], "active": fields[2], "sub": fields[3],
                         "description": fields[4] if len(fields) > 4 else ""})
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump({"services": services[:1000], "count": len(services)}, fh)
print(f"{len(services)} services")
