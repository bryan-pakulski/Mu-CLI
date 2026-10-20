"""Install security updates with the node's package manager (apt, dnf/yum, zypper).

apt: the upgradable packages from a *-security origin are installed together; when apt refuses
the batch (e.g. one needs a removal), each is tried on its own and refusals are reported as
skipped instead of failing everything. Never removes packages. Check mode installs nothing and
does not refresh the package lists.
"""
import json
import os
import shutil
import subprocess

CHECK = os.environ.get("MUCLI_CHECK") == "1"
PARAMS = json.load(open(os.environ["MUCLI_PARAMS"]))
ENV = {"PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"), "LANG": "C",
       "DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "l"}
APT_OPTS = ["-y", "-q", "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"]


def run(argv, timeout=1800):
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=ENV, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def last_line(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1][:300] if lines else "refused"


def reboot_required():
    if os.path.exists("/var/run/reboot-required"):
        return True
    if shutil.which("needs-restarting"):
        return run(["needs-restarting", "-r"], timeout=60)[0] == 1
    return False


def apt():
    if PARAMS.get("refresh", True) and not CHECK:
        run(["apt-get", "update", "-q"], timeout=600)
    _, out, _ = run(["apt", "list", "--upgradable"], timeout=120)
    candidates = sorted({l.split("/")[0] for l in out.splitlines() if "/" in l and "-security" in l.split()[0]})
    installed, skipped = [], []
    if CHECK or not candidates:
        return candidates, installed, skipped
    code, _, err = run(["apt-get", "install", "--only-upgrade", "--no-remove"] + APT_OPTS + candidates)
    if code == 0:
        return candidates, candidates, skipped
    for pkg in candidates:                       # the batch was refused: one at a time
        code, _, err = run(["apt-get", "install", "--only-upgrade", "--no-remove"] + APT_OPTS + [pkg])
        (installed.append(pkg) if code == 0 else skipped.append({"package": pkg, "reason": last_line(err)}))
    return candidates, installed, skipped


def dnf(tool):
    _, out, _ = run([tool, "-q", "updateinfo", "list", "--security"] + ([] if not CHECK else ["--cacheonly"]), timeout=600)
    candidates = sorted({l.split()[-1] for l in out.splitlines() if len(l.split()) >= 3})
    if CHECK or not candidates:
        return candidates, [], []
    code, _, err = run([tool, "-y", "upgrade", "--security"])
    if code == 0:
        return candidates, candidates, []
    return candidates, [], [{"package": p, "reason": last_line(err)} for p in candidates]


def zypper():
    flags = ["-n", "-q"] + (["--no-refresh"] if CHECK or not PARAMS.get("refresh", True) else [])
    _, out, _ = run(["zypper"] + flags + ["list-patches", "--category", "security"], timeout=600)
    candidates = sorted({c.strip() for l in out.splitlines() if l.count("|") >= 4 for c in [l.split("|")[1]] if c.strip() and c.strip() != "Name"})
    if CHECK or not candidates:
        return candidates, [], []
    code, _, err = run(["zypper", "-n", "patch", "--category", "security"])
    if code in (0, 102, 103):                     # 102/103: applied; reboot or restart needed
        return candidates, candidates, []
    return candidates, [], [{"package": p, "reason": last_line(err)} for p in candidates]


manager, result = None, None
for name, fn in (("apt", apt), ("dnf", lambda: dnf("dnf")), ("yum", lambda: dnf("yum")), ("zypper", zypper)):
    if shutil.which(name if name != "apt" else "apt-get"):
        manager, result = name, fn()
        break
if manager is None:
    pms = [p for p in ("pacman", "apk") if shutil.which(p)]
    with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
        json.dump({"manager": pms[0] if pms else None, "check_mode": CHECK, "count": 0, "installed": [], "candidates": [],
                   "skipped": [], "reboot_required": reboot_required()}, fh)
    print(f"{pms[0] if pms else 'This system'} has no separate security channel; use Full upgrade instead.")
    raise SystemExit(2)
candidates, installed, skipped = result
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump({"manager": manager, "check_mode": CHECK, "count": len(candidates if CHECK else installed),
               "installed": installed, "candidates": candidates, "skipped": skipped,
               "reboot_required": reboot_required()}, fh)
verb = "would install" if CHECK else "installed"
print(f"{manager}: {verb} {len(candidates if CHECK else installed)} security update(s)"
      + (f", skipped {len(skipped)}" if skipped else ""))
raise SystemExit(1 if skipped and not installed and not CHECK else 0)
