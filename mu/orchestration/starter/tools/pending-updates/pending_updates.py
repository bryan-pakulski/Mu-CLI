"""Report pending updates from the local package database. Never refreshes or installs."""
import json
import os
import shutil
import subprocess

QUERIES = (
    ("apt", ["apt", "list", "--upgradable"]),
    ("dnf", ["dnf", "-q", "--cacheonly", "check-update"]),
    ("pacman", ["pacman", "-Qu"]),
    ("apk", ["apk", "list", "-u"]),
    ("zypper", ["zypper", "-q", "--no-refresh", "list-updates"]),
)
manager, packages = None, []
for name, argv in QUERIES:
    if not shutil.which(argv[0]):
        continue
    manager = name
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=100, check=False,
                          env={"PATH": os.environ["PATH"], "LANG": "C"})
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(("Listing", "Last metadata", "Loading", "S |", "--")):
            continue
        packages.append(line.split()[0].split("/")[0])
    break
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump({"manager": manager, "count": len(packages), "packages": packages[:500]}, fh)
print(f"{manager or 'no package manager'}: {len(packages)} pending")
