"""Job evidence (mu/jobs/evidence.py): files an agent saves to /output/evidence are kept with the
job, listed in the Checks tab and served safely."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.jobs.core import vm
from mu.jobs.core.artifacts import artifacts_for
from mu.jobs.evidence import collect_for_stage, kind_of, manifest, resolve
from test_job_worker import running_job

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def _outputs(tmp_path) -> Path:
    out = tmp_path / "out"
    ev = out / "evidence"
    (ev / "pages").mkdir(parents=True)
    (ev / "pages" / "home.png").write_bytes(PNG)
    (ev / "run.log").write_text("ok\nAWS_SECRET_ACCESS_KEY=abcd1234abcd1234abcd1234abcd1234abcd1234\n")
    (ev / "page.html").write_text("<script>alert(1)</script>")
    (ev / ".hidden").write_text("x")
    os.symlink("/etc/passwd", ev / "passwd")
    (ev / "index.json").write_text('[{"file": "pages/home.png", "caption": "Home after the change"}]')
    return out


def test_collect_copies_regular_files_with_captions_and_redacts_text(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    art = artifacts_for(job).ensure()
    entries = collect_for_stage(service, job, _outputs(tmp_path), art, "implement")
    by_name = {e["name"]: e for e in entries}
    assert set(by_name) == {"home.png", "run.log", "page.html"}  # no symlink, dotfile or index
    assert by_name["home.png"]["kind"] == "image" and by_name["home.png"]["caption"] == "Home after the change"
    assert by_name["page.html"]["kind"] == "text"
    log = Path(art.path("evidence", *by_name["run.log"]["file"].split("/"))).read_text()
    assert "abcd1234" not in log and "REDACTED" in log
    assert [e["file"] for e in manifest(art)] == [e["file"] for e in entries]
    assert [e.event_type for e in service.events(job.id)][-1] == "evidence_added"
    assert resolve(art, by_name["home.png"]["file"])["path"].endswith("home.png")
    assert resolve(art, "../jobs.sqlite3") is None and resolve(art, "nope.png") is None


def test_no_evidence_directory_is_a_no_op(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    assert collect_for_stage(service, job, tmp_path / "missing", artifacts_for(job).ensure(), "implement") == []


def test_checks_part_and_notes_show_evidence(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    collect_for_stage(service, job, _outputs(tmp_path), artifacts_for(job).ensure(), "review")
    data = vm.job_vm(service, job.id, parts=["checks", "notes"])
    assert len(data["checks"]["evidence"]) == 3
    note = data["notes"][-1]
    assert note["title"] == "Evidence captured (3 files)" and note["tab"] == "checks"
    assert note["bullets"] == ["Home after the change"]


def test_the_endpoint_serves_images_inline_and_everything_else_as_a_download(tmp_path):
    from mu.gui.routers import jobs as jobs_router
    service, _, job, _, _ = running_job(tmp_path)
    entries = {e["name"]: e for e in collect_for_stage(service, job, _outputs(tmp_path),
                                                       artifacts_for(job).ensure(), "implement")}
    app = FastAPI()
    app.state.job_service = service
    app.include_router(jobs_router.router, prefix="/api/jobs")
    client = TestClient(app)
    base = f"/api/jobs/{job.id}/evidence/"
    image = client.get(base + entries["home.png"]["file"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.headers["content-disposition"].startswith("inline")
    html = client.get(base + entries["page.html"]["file"])
    assert html.headers["content-type"].startswith("text/plain") and html.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in html.headers["content-security-policy"] and html.headers["x-content-type-options"] == "nosniff"
    assert client.get(base + "../../jobs.sqlite3").status_code == 404


def test_agents_are_asked_for_evidence(tmp_path):
    from mu.jobs.runner import implement_prompt
    from mu.jobs.stages import build_stage_review_prompt
    service, _, job, _, _ = running_job(tmp_path)
    assert "/output/evidence/" in implement_prompt(service, job)
    assert "/output/evidence/" in build_stage_review_prompt(job, "", "", 1)


def test_kinds():
    assert [kind_of(n) for n in ("a.png", "b.webm", "c.svg", "d.bin")] == ["image", "video", "text", "file"]
