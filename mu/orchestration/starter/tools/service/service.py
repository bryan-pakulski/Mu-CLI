"""Restart, reload, start or stop one systemd unit; report its state afterwards."""
import json
import os
import subprocess

CHECK = os.environ.get("MUCLI_CHECK") == "1"
PARAMS = json.load(open(os.environ["MUCLI_PARAMS"]))
ENV = {"PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"), "LANG": "C"}
unit = str(PARAMS["unit"])
if "." not in unit.split("@")[-1]:
    unit += ".service"
action = str(PARAMS.get("action") or "restart")


def show():
    out = subprocess.run(["systemctl", "show", unit, "-p", "LoadState,ActiveState,SubState"], capture_output=True,
                         text=True, timeout=30, env=ENV).stdout
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return props.get("LoadState", ""), props.get("ActiveState", ""), props.get("SubState", "")


load, active, sub = show()
code = 0
if load != "loaded":
    print(f"{unit} is not a loaded unit ({load or 'unknown'})")
    code = 2
elif not CHECK:
    proc = subprocess.run(["systemctl", action, unit], capture_output=True, text=True, timeout=240, env=ENV)
    code = proc.returncode
    if code:
        print((proc.stderr or proc.stdout).strip()[-1000:])
    _, active, sub = show()
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump({"unit": unit, "action": action, "active": active, "sub": sub, "check_mode": CHECK}, fh)
print(f"{unit}: {'would ' + action if CHECK else action} → {active} ({sub})")
raise SystemExit(code)
