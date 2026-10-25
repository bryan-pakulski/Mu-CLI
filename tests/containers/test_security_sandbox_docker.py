"""Mandatory Docker proof of PoC network, filesystem and credential isolation."""
from pathlib import Path

from docker_helpers import require_runtime_image
from security_helpers import finding, scan_session
from mu.security import engine


def test_security_poc_uses_disposable_networkless_copy(tmp_path):
    require_runtime_image()
    session = scan_session(tmp_path)
    report, f = finding(session)
    engine.configure_runner(session, "docker", actor="user:cli")
    outside = tmp_path / "outside.txt"
    outside.write_text("host untouched")
    root = Path(report.workspace_root)
    (root / ".env").write_text("SECRET_TOKEN=fake-only")
    (root / "probe.py").write_text(f'''from pathlib import Path
import socket
import json
print('WORKSPACE_FILES=' + json.dumps(sorted(p.name for p in Path('.').iterdir())))
assert not Path('.git').exists()
assert Path('/proc/net/route').read_text().strip().count('\\n') == 0
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('network reachable')
Path('app.py').write_text('changed inside container')
external = Path({str(outside)!r})
external.parent.mkdir(parents=True, exist_ok=True)
external.write_text('only inside container')
print('PROBE-RAN EXPLOIT')
''')
    proof = engine.verify_proof(f, report=report, owner=session)
    assert proof.verified and f.runs[-1]["runner"] == "docker"
    # Assert the same exclusion on the actual container listing. Referencing the
    # protected filename inside the PoC is itself refused by the secret guard.
    import json
    listing = next(line for line in proof.captured_stdout.splitlines() if line.startswith("WORKSPACE_FILES="))
    container_files = json.loads(listing.split("=", 1)[1])
    assert ".env" not in container_files
    assert "app.py" in container_files and "probe.py" in container_files
    assert outside.read_text() == "host untouched"
    assert (root / "app.py").read_text() == "VULNERABLE = True\n"
    assert (root / ".env").exists()
