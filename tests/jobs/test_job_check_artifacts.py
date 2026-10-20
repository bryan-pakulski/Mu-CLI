"""Check run artifacts: everything a suite writes is kept (untracked or ignored, never tracked
files, caches or symlinks), text is secret-redacted, and the viewer gets a preview per kind —
logs, JUnit, XML, JSON, CSV, packet captures, archives — served safely."""

from __future__ import annotations

import io
import os
import socket
import struct
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.jobs import check_artifacts as ca
from mu.jobs.checks import ChecksStage
from mu.jobs.core.artifacts import artifacts_for
from job_helpers import git, running_job

ROOT = Path(__file__).resolve().parents[2]
SECRET = "AWS_SECRET_ACCESS_KEY=abcd1234abcd1234abcd1234abcd1234abcd1234"
JUNIT = ('<?xml version="1.0"?><testsuites><testsuite name="unit" time="0.5">'
         '<testcase classname="pkg.mod" name="test_ok" time="0.1"/>'
         '<testcase classname="pkg.mod" name="test_bad" time="0.2"><failure message="assert 1 == 2">trace here</failure></testcase>'
         '<testcase classname="pkg.mod" name="test_skip"><skipped/></testcase></testsuite></testsuites>')


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def _suite_writes(job, workspace, suite):
    """A suite that leaves the usual mess behind: reports in an ignored dir, a log, a cache,
    a capture, an edit to a tracked file and a symlink."""
    w = Path(workspace)
    (w / "reports").mkdir(exist_ok=True)
    (w / "reports" / "junit.xml").write_text(JUNIT)
    (w / "test.log").write_text(f"starting\nERROR boom\n{SECRET}\n")
    (w / "node_modules" / "x").mkdir(parents=True, exist_ok=True)
    (w / "node_modules" / "x" / "index.js").write_text("cache")
    (w / "__pycache__").mkdir(exist_ok=True)
    (w / "__pycache__" / "m.pyc").write_bytes(b"\0")
    (w / "capture.pcap").write_bytes(_pcap())
    os.symlink("/etc/passwd", w / "passwd-link")
    return {"passed": True, "return_code": 0, "stdout": "ok", "stderr": ""}


def test_a_checks_run_keeps_what_its_suites_produced(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    (Path(job.worktree) / ".gitignore").write_text("reports/\n")
    git(job.worktree, "add", ".gitignore")
    git(job.worktree, "commit", "-m", "ignore reports")
    job = replace(service.get(job.id), validation_commands=["pytest -q"])
    run = ChecksStage(service, run_suite=_suite_writes, settings={}).verify(job)
    art = artifacts_for(job)
    listing = ca.run_listing(art, run.summary["run"])
    names = {f["name"]: f for f in listing["files"] if f["path"].startswith("artifacts/")}
    assert set(names) == {"reports/junit.xml", "test.log", "capture.pcap"}       # ignored files too
    assert names["reports/junit.xml"]["kind"] == "junit" and names["capture.pcap"]["kind"] == "pcap"
    assert names["test.log"]["label"] == "suite-1" and len(names["test.log"]["suites"]) == 1
    kept = Path(art.path("checks", f"run-{run.summary['run']}", *names["test.log"]["path"].split("/"))).read_text()
    assert "abcd1234" not in kept and "REDACTED" in kept and names["test.log"]["redacted"] == 1
    assert not (Path(job.worktree) / "test.log").exists()                       # still discarded from the tree
    assert any(f["path"] == "suite-1.log" and f["kind"] == "log" for f in listing["files"])


def test_tracked_edits_old_files_and_caps(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    w = Path(job.worktree)
    art = artifacts_for(job).ensure()
    run_no = art.next_check_run()
    old = w / "old.txt"
    old.write_text("before the suite")
    os.utime(old, (1, 1))
    tracked = ca.tracked_files(job.worktree)
    some_tracked = sorted(tracked)[0]
    (w / some_tracked).write_text("edited by a test")
    (w / "big.bin").write_bytes(b"x" * 10)
    import time as _t
    out = ca.collect(job.worktree, art, run_no, "suite-1", since=_t.time() - 60, tracked=tracked,
                     budget={"bytes": ca.MAX_RUN_BYTES - 5})
    assert [e["name"] for e in out["entries"]] == []
    assert {s["name"] for s in out["skipped"]} == {"big.bin"}                    # over the run budget
    assert "old.txt" not in {s["name"] for s in out["skipped"]}                  # too old: not this suite's


# --------------------------------------------------------------------- previews

def _entry(tmp_path, name, data, kind=None):
    p = tmp_path / name
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return {"full": str(p), "path": name, "name": name, "kind": kind or ca.classify(name, data[:4096] if isinstance(data, bytes) else data.encode()[:4096])}


@pytest.mark.parametrize("name,data,kind", [
    ("a.log", "x", "log"), ("r.xml", JUNIT, "junit"), ("c.xml", "<?xml version='1.0'?><coverage/>", "xml"),
    ("d.json", "{}", "json"), ("e.csv", "a,b", "csv"), ("i.html", "<p>", "html"), ("s.png", b"\x89PNG\r\n\x1a\n", "image"),
    ("t.pcapng", b"\x0a\x0d\x0d\x0a", "pcap"), ("z.zip", b"PK\x03\x04", "archive"), ("bin.dat", b"\x00\x01\x02", "binary"),
    ("noext", "plain words\n", "text"),
])
def test_classify(name, data, kind):
    assert ca.classify(name, data if isinstance(data, bytes) else data.encode()) == kind


def test_junit_preview_lists_cases(tmp_path):
    p = ca.build_preview(_entry(tmp_path, "r.xml", JUNIT))
    assert p["junit"]["totals"] == {"tests": 3, "failures": 1, "errors": 0, "skipped": 1}
    bad = [c for c in p["junit"]["suites"][0]["cases"] if c["status"] == "failed"][0]
    assert bad["name"] == "test_bad" and bad["message"] == "assert 1 == 2" and bad["detail"] == "trace here"
    assert p["pretty"] and "<testcase" in p["text"]


def test_xml_with_entities_is_never_parsed(tmp_path):
    bomb = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><testsuite>&b;</testsuite>'
    p = ca.build_preview(_entry(tmp_path, "bomb.xml", bomb))
    assert "junit" not in p and "DOCTYPE" in p["note"] and "&b;" in p["text"]


def test_text_json_csv_archive_and_hex(tmp_path):
    big = "line\n" * (ca.PREVIEW_TEXT_BYTES // 2)
    p = ca.build_preview(_entry(tmp_path, "big.log", big))
    assert p["truncated"] and "bytes not shown" in p["text"]
    assert ca.build_preview(_entry(tmp_path, "d.json", '{"a":[1,2]}'))["text"].startswith("{\n  \"a\"")
    assert ca.build_preview(_entry(tmp_path, "e.csv", "h1,h2\n1,2\n"))["rows"] == [["h1", "h2"], ["1", "2"]]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dir/report.txt", "hello")
    p = ca.build_preview(_entry(tmp_path, "a.zip", buf.getvalue()))
    assert p["archive"] == "zip" and p["entries"][0] == {"name": "dir/report.txt", "bytes": 5, "dir": False}
    assert ca.build_preview(_entry(tmp_path, "b.dat", b"\x00\x01AB"))["hex"].startswith("00000000  00 01 41 42")
    broken = ca.build_preview(_entry(tmp_path, "x.json", "{nope"))
    assert broken["text"] == "{nope"                                             # shown as text, not an error


# --------------------------------------------------------------------- packet captures

def _udp_dns(src="10.0.0.2", dst="10.0.0.1"):
    q = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0) + b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
    udp = struct.pack("!HHHH", 53000, 53, 8 + len(q), 0) + q
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 1, 0, 64, 17, 0, socket.inet_aton(src), socket.inet_aton(dst)) + udp
    return b"\xaa" * 6 + b"\xbb" * 6 + b"\x08\x00" + ip


def _tcp_syn(src="10.0.0.2", dst="10.0.0.3"):
    tcp = struct.pack("!HHIIBBHHH", 40000, 443, 1, 0, 0x50, 0x02, 65535, 0, 0)
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 1, 0, 64, 6, 0, socket.inet_aton(src), socket.inet_aton(dst)) + tcp
    return b"\xaa" * 6 + b"\xbb" * 6 + b"\x08\x00" + ip


def _pcap():
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    for i, frame in enumerate([_udp_dns(), _tcp_syn()]):
        out += struct.pack("<IIII", 1000 + i, 500, len(frame), len(frame)) + frame
    return out


def _pcapng():
    def block(btype, body):
        body += b"\0" * ((4 - len(body) % 4) % 4)
        n = 12 + len(body)
        return struct.pack("<II", btype, n) + body + struct.pack("<I", n)
    shb = block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    idb = block(1, struct.pack("<HHI", 1, 0, 65535))
    frame = _tcp_syn()
    epb = block(6, struct.pack("<IIIII", 0, 0, 1_000_000, len(frame), len(frame)) + frame)
    return shb + idb + epb


def test_pcap_packets_protocols_and_conversations(tmp_path):
    p = ca.read_pcap(str(tmp_path / "c.pcap") if (tmp_path / "c.pcap").write_bytes(_pcap()) else "")
    assert p["format"] == "pcap" and p["total"] == 2
    dns, syn = p["packets"]
    assert dns["proto"] == "DNS" and dns["info"] == "query example.com" and dns["src"] == "10.0.0.2:53000"
    assert syn["proto"] == "TCP" and "[SYN]" in syn["info"] and syn["dst"] == "10.0.0.3:443"
    assert p["protocols"] == {"DNS": 1, "TCP": 1} and len(p["conversations"]) == 2
    assert syn["time"] == pytest.approx(1.0)


def test_pcapng_and_garbage(tmp_path):
    (tmp_path / "c.pcapng").write_bytes(_pcapng())
    p = ca.read_pcap(str(tmp_path / "c.pcapng"))
    assert p["format"] == "pcapng" and p["packets"][0]["proto"] == "TCP"
    garbage = ca.build_preview(_entry(tmp_path, "bad.pcap", b"\xd4\xc3\xb2\xa1" + b"\xff" * 10))
    assert "error" in garbage or garbage.get("packets") == []                  # never raises


# --------------------------------------------------------------------- the routes

def _client(service):
    from mu.gui.routers import jobs as jobs_router
    app = FastAPI()
    app.state.job_service = service
    app.include_router(jobs_router.router, prefix="/api/jobs")
    return TestClient(app)


def test_routes_list_preview_and_serve_safely(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    w = Path(job.worktree)

    def suite(job, workspace, spec):
        (Path(workspace) / "out").mkdir(exist_ok=True)
        (Path(workspace) / "out" / "report.html").write_text("<script>alert(1)</script><link rel=stylesheet href=s.css>")
        (Path(workspace) / "out" / "s.css").write_text("p{}")
        (Path(workspace) / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
        return {"passed": True, "return_code": 0, "stdout": "fine", "stderr": "", "junit_xml": JUNIT}
    job = replace(job, validation_commands=["pytest -q"])
    run_no = ChecksStage(service, run_suite=suite, settings={}).verify(job).summary["run"]
    client = _client(service)
    base = f"/api/jobs/{job.id}/checks/{run_no}"
    files = {f["path"]: f for f in client.get(f"{base}/artifacts").json()["files"]}
    assert {"suite-1.log", "suite-1.xml", "artifacts/suite-1/out/report.html", "artifacts/suite-1/shot.png"} <= set(files)
    assert client.get(f"{base}/preview", params={"path": "suite-1.xml"}).json()["junit"]["totals"]["failures"] == 1
    html = client.get(f"{base}/files/artifacts/suite-1/out/report.html")
    assert html.headers["content-disposition"].startswith("attachment")                 # never a live page
    assert html.headers["content-type"] == "application/octet-stream"
    framed = client.get(f"{base}/files/artifacts/suite-1/out/report.html", params={"view": "1"})
    assert framed.headers["content-type"].startswith("text/html") and "sandbox" in framed.headers["content-security-policy"]
    assert "script-src" not in framed.headers["content-security-policy"] and framed.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"{base}/files/artifacts/suite-1/out/s.css").headers["content-type"].startswith("text/css")
    image = client.get(f"{base}/files/artifacts/suite-1/shot.png")
    assert image.headers["content-type"] == "image/png" and image.headers["content-disposition"].startswith("inline")
    for bad in ("../summary.json", "../../jobs.sqlite3", "artifacts/../../ticket.json", "nope.txt", "summary.json"):
        assert client.get(f"{base}/files/{bad}").status_code == 404, bad
        assert client.get(f"{base}/preview", params={"path": bad}).status_code == 404, bad
    assert client.get(f"/api/jobs/{job.id}/checks/99/artifacts").status_code == 404

