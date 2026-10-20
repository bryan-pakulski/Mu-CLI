"""Upgrade every package with the node's package manager. No removals unless allow_removals.
Check mode lists what would be upgraded from the current package lists and changes nothing."""
import json
import os
import re
import shutil
import subprocess

CHECK = os.environ.get("MUCLI_CHECK") == "1"
PARAMS = json.load(open(os.environ["MUCLI_PARAMS"]))
REMOVALS = bool(PARAMS.get("allow_removals"))
ENV = {"PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"), "LANG": "C",
       "DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "l"}
APT_OPTS = ["-y", "-q", "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"]


def run(argv, timeout=3600):
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=ENV, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def reboot_required():
    if os.path.exists("/var/run/reboot-required"):
        return True
    if shutil.which("needs-restarting"):
        return run(["needs-restarting", "-r"], timeout=60)[0] == 1
    return False


def apt():
    if not CHECK:
        run(["apt-get", "update", "-q"], timeout=600)
    verb = "full-upgrade" if REMOVALS else "upgrade"
    _, out, _ = run(["apt-get", "-s", verb], timeout=600)
    pkgs = sorted({l.split()[1] for l in out.splitlines() if l.startswith("Inst ")})
    if CHECK or not pkgs:
        return pkgs, 0, ""
    code, _, err = run(["apt-get", verb] + APT_OPTS)
    return pkgs, code, err


def dnf(tool):
    _, out, _ = run([tool, "-q", "check-update"] + (["--cacheonly"] if CHECK else []), timeout=600)
    pkgs = sorted({l.split()[0] for l in out.splitlines() if re.match(r"^\S+\.\S+\s+\S+\s+\S+", l)})
    if CHECK or not pkgs:
        return pkgs, 0, ""
    code, _, err = run([tool, "-y", "upgrade"] + (["--allowerasing"] if REMOVALS else []))
    return pkgs, code, err


def zypper():
    _, out, _ = run(["zypper", "-n", "-q"] + (["--no-refresh"] if CHECK else []) + ["list-updates"], timeout=600)
    pkgs = sorted({l.split("|")[2].strip() for l in out.splitlines() if l.count("|") >= 4 and l.split("|")[2].strip() not in ("", "Name")})
    if CHECK or not pkgs:
        return pkgs, 0, ""
    code, _, err = run(["zypper", "-n", "update"] + (["--allow-vendor-change"] if REMOVALS else []))
    return pkgs, (0 if code in (102, 103) else code), err


def pacman():
    tool = ["checkupdates"] if shutil.which("checkupdates") else ["pacman", "-Qu"]
    _, out, _ = run(tool, timeout=600)
    pkgs = sorted({l.split()[0] for l in out.splitlines() if l.strip()})
    if CHECK or not pkgs:
        return pkgs, 0, ""
    code, _, err = run(["pacman", "-Syu", "--noconfirm"])
    return pkgs, code, err


def apk():
    if not CHECK:
        run(["apk", "update"], timeout=600)
    _, out, _ = run(["apk", "list", "-u"], timeout=600)
    pkgs = sorted({l.split()[0] for l in out.splitlines() if l.strip()})
    if CHECK or not pkgs:
        return pkgs, 0, ""
    code, _, err = run(["apk", "upgrade"] + (["--available"] if REMOVALS else []))
    return pkgs, code, err


manager, result = None, None
for name, fn in (("apt", apt), ("dnf", lambda: dnf("dnf")), ("yum", lambda: dnf("yum")), ("zypper", zypper),
                 ("pacman", pacman), ("apk", apk)):
    if shutil.which(name if name != "apt" else "apt-get"):
        manager, result = name, fn()
        break
pkgs, code, err = result if result else ([], 2, "no supported package manager")
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump({"manager": manager, "check_mode": CHECK, "count": len(pkgs), "packages": pkgs[:1000],
               "removals_allowed": REMOVALS, "reboot_required": reboot_required()}, fh)
if code:
    print(f"{manager or 'upgrade'} failed: " + "\n".join((err or "").strip().splitlines()[-20:]))
    raise SystemExit(code if 0 < code < 125 else 1)
print(f"{manager}: {'would upgrade' if CHECK else 'upgraded'} {len(pkgs)} package(s)")
