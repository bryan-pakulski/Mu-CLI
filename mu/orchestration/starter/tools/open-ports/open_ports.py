"""List listening sockets. Read-only: ss -H -l -t -u -n -p."""
import json
import os
import re
import subprocess

proc = subprocess.run(["ss", "-H", "-l", "-t", "-u", "-n", "-p"], capture_output=True, text=True,
                      timeout=20, check=False)
listening = []
for line in proc.stdout.splitlines():
    fields = line.split()
    if len(fields) < 5:
        continue
    local = fields[4]
    address, _, port = local.rpartition(":")
    match = re.search(r'users:\(\("([^"]+)"', line)
    listening.append({"proto": fields[0], "address": address, "port": int(port) if port.isdigit() else None,
                      "process": match.group(1) if match else ""})
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump({"listening": listening, "count": len(listening)}, fh)
print(f"{len(listening)} listening socket(s)")
