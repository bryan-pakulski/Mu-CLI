"""Jobs never fail an agent's honest work over arbitrary size or count limits: big JUnit
reports, long check output, and setups or repairs that leave many files are all handled."""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from mu.jobs.checks import collapse_untracked, parse_junit
from mu.jobs.verification import _CappedReader


def _junit(cases: int, pad: int = 0) -> str:
    body = "".join(f'<testcase classname="t" name="case{i}"><system-out>{"x" * pad}</system-out></testcase>'
                   for i in range(cases))
    return f'<?xml version="1.0"?><testsuite name="s">{body}</testsuite>'


def test_a_junit_report_of_any_size_parses():
    text = _junit(2000, pad=6000)  # ~12 MB, above the old 8 MiB cap
    assert len(text) > 8 * 1024 * 1024
    result = parse_junit(text)
    assert result["total"] == 2000 and result["failed"] == 0


def test_junit_entity_declarations_are_still_refused():
    import pytest
    for bad in ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><testsuite/>',
                '<!ENTITY x SYSTEM "file:///etc/passwd"><testsuite/>'):
        with pytest.raises(ValueError, match="DOCTYPE or ENTITY"):
            parse_junit(bad)


def test_check_output_within_the_cap_is_kept_whole_and_beyond_it_keeps_the_head():
    lines = "".join(f"line {i}\n" for i in range(1000))
    reader = _CappedReader(io.StringIO(lines), cap=1 << 20)
    reader.start()
    out, truncated = reader.join()
    assert out == lines and truncated is False  # previously only the last 200 lines survived
    reader = _CappedReader(io.StringIO(lines), cap=2000)
    reader.start()
    out, truncated = reader.join()
    assert truncated is True and out.startswith("line 0\n") and out.rstrip().endswith("line 999")
    assert "bytes left out" in out and len(out) < 2200


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("x = 1\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    return repo


def test_many_produced_files_collapse_to_their_untracked_directories(tmp_path):
    repo = _repo(tmp_path)
    produced = [f"vendor/pkg{i}/file.js" for i in range(700)] + ["src/gen.py", "top.log"]
    assert collapse_untracked(str(repo), produced[:3]) == sorted(produced[:3])  # small lists stay exact
    collapsed = collapse_untracked(str(repo), produced)
    assert collapsed == ["src/gen.py", "top.log", "vendor/"]  # src/ holds tracked files: kept per file


def test_the_dirty_tree_filter_honours_collapsed_directories(tmp_path):
    from types import SimpleNamespace
    from mu.jobs import checks
    job = SimpleNamespace(metadata={"environment_setup": {"node": {"status": "ok", "files": ["vendor/"]}}})
    porcelain = "?? vendor/pkg1/\n?? vendor/\n M src/app.py"
    assert checks._without_setup_files(job, porcelain) == " M src/app.py"
