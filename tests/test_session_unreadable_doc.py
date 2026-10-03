"""SessionManager must never clobber a session.json it cannot read.

Regression for the container-mode "functions: []" bug: a root worker made
the bind-mounted session.json unreadable by the host, the host treated it as
missing, reset variables to workspace defaults, and overwrote the file.
"""

from __future__ import annotations

import json
import os

import pytest

from mu.session.manager import SessionManager, SessionUnreadableError


def _write_doc(home, name, variables):
    d = home / "sessions" / name
    d.mkdir(parents=True)
    p = d / "session.json"
    p.write_text(json.dumps({"history": [], "variables": variables, "revision": 7}))
    return p


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_unreadable_doc_raises_and_is_never_clobbered(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path))
    p = _write_doc(tmp_path, "boxed", {"session_type": "container", "yolo": True})
    before = p.read_bytes()
    p.chmod(0)
    try:
        with pytest.raises(SessionUnreadableError):
            SessionManager(session_name="boxed")
    finally:
        p.chmod(0o600)
    assert p.read_bytes() == before, "load must not touch the document"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_save_history_refuses_after_failed_load(tmp_path, monkeypatch):
    """Even if a caller swallows the load error, saving must not overwrite."""
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path))
    p = _write_doc(tmp_path, "boxed", {"session_type": "container"})
    before = p.read_bytes()
    p.chmod(0)
    try:
        sm = SessionManager()
        try:
            sm._load_session("boxed")
        except SessionUnreadableError:
            pass
        sm.current_session_name = "boxed"
        with pytest.raises(SessionUnreadableError):
            sm.save_history()
    finally:
        p.chmod(0o600)
    assert p.read_bytes() == before


def test_missing_doc_still_yields_fresh_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path))
    sm = SessionManager(session_name="brandnew")
    assert sm.history == []
    assert sm.variables.get("session_type", "workspace") == "workspace"
    assert sm._load_failed is False
    # and saving a genuinely new session is still allowed
    sm.save_history()
    assert (tmp_path / "sessions" / "brandnew" / "session.json").is_file()


def test_corrupt_doc_keeps_legacy_behaviour(tmp_path, monkeypatch):
    """JSONDecodeError path unchanged: treated as empty, not as unreadable."""
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path))
    d = tmp_path / "sessions" / "broken"
    d.mkdir(parents=True)
    (d / "session.json").write_text("{not json")
    sm = SessionManager(session_name="broken")
    assert sm.history == []
    assert sm._load_failed is False
