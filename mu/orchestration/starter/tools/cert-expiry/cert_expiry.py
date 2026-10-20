"""Report certificate expiry for PEM files under the given paths. Read-only."""
import datetime
import json
import os
import subprocess

with open(os.environ["MUCLI_PARAMS"], encoding="utf-8") as fh:
    params = json.load(fh)
warn = params.get("warn_days", 30)
certs = []
now = datetime.datetime.now(datetime.timezone.utc)
for root in params.get("paths", []):
    if not os.path.isdir(root):
        continue
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith((".pem", ".crt", ".cer")) or "privkey" in name or name.endswith(".key"):
                continue
            path = os.path.join(dirpath, name)
            proc = subprocess.run(["openssl", "x509", "-noout", "-enddate", "-subject", "-in", path],
                                  capture_output=True, text=True, timeout=10, check=False)
            if proc.returncode != 0:
                continue
            fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
            end = fields.get("notAfter", "").strip()
            try:
                expires = datetime.datetime.strptime(end, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                continue
            certs.append({"path": path, "subject": fields.get("subject", "").strip(),
                          "not_after": expires.isoformat(), "days_left": (expires - now).days})
            if len(certs) >= 500:
                break
certs.sort(key=lambda c: c["days_left"])
result = {"certs": certs, "expiring": sum(1 for c in certs if c["days_left"] <= warn)}
with open(os.environ["MUCLI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump(result, fh)
print(f"{len(certs)} certificate(s), {result['expiring']} expiring within {warn} days")
