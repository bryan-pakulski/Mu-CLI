"""Ownership-preserving atomic writes (mu/session/fileowner).

A root container worker saving into a bind-mounted host directory must not
leave root-owned 0600 inodes behind, or the host process loses the session.
"""

from __future__ import annotations

import os

import pytest

from mu.session import fileowner


def _patch_root(monkeypatch, calls):
    monkeypatch.setattr(fileowner, "_euid", lambda: 0)
    monkeypatch.setattr(fileowner.os, "fchown", lambda fd, u, g: calls.append(("fchown", fd, u, g)))
    monkeypatch.setattr(fileowner.os, "fchmod", lambda fd, m: calls.append(("fchmod", fd, m)))
    monkeypatch.setattr(fileowner.os, "chown", lambda p, u, g: calls.append(("chown", p, u, g)))
    monkeypatch.setattr(fileowner.os, "chmod", lambda p, m: calls.append(("chmod", p, m)))


def test_match_owner_copies_existing_target_uid_gid_mode(tmp_path, monkeypatch):
    target = tmp_path / "session.json"
    target.write_text("{}")
    os.chmod(target, 0o640)
    st = os.stat(target)
    tmp = tmp_path / ".session-x.tmp"
    tmp.write_text("{}")
    calls = []
    _patch_root(monkeypatch, calls)

    fd = os.open(tmp, os.O_RDWR)
    try:
        assert fileowner.match_owner(fd, str(target), default_mode=0o600) is True
    finally:
        os.close(fd)
    assert ("fchown", fd, st.st_uid, st.st_gid) in calls
    assert ("fchmod", fd, 0o640) in calls


def test_match_owner_falls_back_to_parent_dir_owner_and_default_mode(tmp_path, monkeypatch):
    target = tmp_path / "missing.json"
    parent = os.stat(tmp_path)
    tmp = tmp_path / "stage.tmp"
    tmp.write_text("x")
    calls = []
    _patch_root(monkeypatch, calls)

    assert fileowner.match_owner(str(tmp), str(target), default_mode=0o600) is True
    assert ("chown", str(tmp), parent.st_uid, parent.st_gid) in calls
    assert ("chmod", str(tmp), 0o600) in calls


def test_match_owner_noop_when_not_root(tmp_path, monkeypatch):
    target = tmp_path / "session.json"
    target.write_text("{}")
    calls = []
    _patch_root(monkeypatch, calls)
    monkeypatch.setattr(fileowner, "_euid", lambda: 1000)

    assert fileowner.match_owner(str(target), str(target)) is False
    assert calls == []


def test_match_owner_swallows_oserror(tmp_path, monkeypatch):
    target = tmp_path / "session.json"
    target.write_text("{}")
    monkeypatch.setattr(fileowner, "_euid", lambda: 0)

    def boom(*_a, **_k):
        raise PermissionError("chown denied")

    monkeypatch.setattr(fileowner.os, "chown", boom)
    assert fileowner.match_owner(str(target), str(target)) is False


def test_match_owner_returns_false_when_nothing_statable(monkeypatch):
    monkeypatch.setattr(fileowner, "_euid", lambda: 0)
    assert fileowner.match_owner("/nonexistent/dir/x.tmp", "/nonexistent/dir/y.json") is False


# --------------------------------------------------------------- integration


def test_trace_emitter_preserves_owner_on_first_open(tmp_path, monkeypatch):
    """Root worker opening a trace JSONL in a host-owned dir chowns it."""
    from mu.trace import emitter as em

    calls = []
    monkeypatch.setattr(em, "preserve_owner", lambda p: calls.append(p) or True)
    path = str(tmp_path / "t_run_abc.jsonl")
    e = em.TraceEmitter("t", "run_abc", path)
    e._open()
    try:
        assert calls == [path]
    finally:
        e.close()


def test_presence_beacon_matches_owner(tmp_path, monkeypatch):
    """Presence beacon (sessions/<n>/presence/<pid>.json) goes through match_owner."""
    from mu.session import presence as pr

    monkeypatch.setattr(pr, "_history_dir", lambda: str(tmp_path))
    calls = []
    monkeypatch.setattr(pr, "match_owner", lambda fd, path: calls.append(path) or True)
    out = pr.write_beacon("sess", "gui")
    assert calls == [out], "match_owner must target the final beacon path"
    assert os.path.isfile(out)
