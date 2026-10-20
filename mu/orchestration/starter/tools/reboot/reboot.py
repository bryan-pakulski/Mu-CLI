"""Schedule a reboot. The reboot runs from its own systemd timer a few seconds later, outside
this execution, so the run reports success before the machine goes down."""
import json
import os
import platform
import re
import shutil
import subprocess
import time

CHECK = os.environ.get("MUCLI_CHECK") == "1"
PARAMS = json.load(open(os.environ["MUCLI_PARAMS"]))
ENV = {"PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"), "LANG": "C"}
DELAY = max(10, min(3600, int(PARAMS.get("delay_s") or 30)))
MESSAGE = str(PARAMS.get("message") or "μcli maintenance reboot")[:200]


def key(text):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text)]


def reasons():
    out = []
    if os.path.exists("/var/run/reboot-required"):
        out.append("the package manager requested a reboot")
    if shutil.which("needs-restarting"):
        try:
            if subprocess.run(["needs-restarting", "-r"], capture_output=True, timeout=60, env=ENV).returncode == 1:
                out.append("needs-restarting reports core libraries or the kernel were updated")
        except (OSError, subprocess.SubprocessError):
            pass
    running = platform.release()
    base = "/lib/modules" if os.path.isdir("/lib/modules") else "/usr/lib/modules"
    try:
        installed = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    except OSError:
        installed = []
    if installed:
        flavour = running.split("-")[-1] if "-" in running else ""
        if "." in flavour:
            flavour = flavour.rsplit(".", 1)[-1]
        newest = max([k for k in installed if not flavour or k.endswith(flavour)] or installed, key=key)
        if running not in installed:
            out.append(f"the running kernel {running} was removed; {newest} is installed")
        elif key(newest) > key(running):
            out.append(f"kernel {newest} is installed; {running} is running")
    return out


why = reasons()
required = bool(why)
should = required or not PARAMS.get("only_if_required")
scheduled, at = False, None
if should and not CHECK:
    unit = f"mucli-reboot-{int(time.time())}"
    proc = subprocess.run(["systemd-run", f"--unit={unit}", f"--on-active={DELAY}s", "--timer-property=AccuracySec=1s",
                           "--description", MESSAGE, "systemctl", "reboot"], capture_output=True, text=True, env=ENV)
    if proc.returncode != 0:                       # no transient timers: fall back to shutdown(8)
        proc = subprocess.run(["shutdown", "-r", f"+{max(1, round(DELAY / 60))}", MESSAGE],
                              capture_output=True, text=True, env=ENV)
    scheduled = proc.returncode == 0
    at = time.time() + DELAY if scheduled else None
    if not scheduled:
        print("could not schedule the reboot:", (proc.stderr or proc.stdout).strip()[-500:])
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump({"scheduled": scheduled, "required": required, "reasons": why, "delay_s": DELAY, "at": at,
               "check_mode": CHECK}, fh)
if CHECK:
    print(("would reboot" if should else "would not reboot (not required)") + (": " + "; ".join(why) if why else ""))
elif not should:
    print("no reboot needed; nothing scheduled")
elif scheduled:
    print(f"reboot scheduled in {DELAY}s" + (": " + "; ".join(why) if why else ""))
else:
    raise SystemExit(1)
