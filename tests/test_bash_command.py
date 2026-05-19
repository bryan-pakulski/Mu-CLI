"""Tests for the user-facing /bash slash command."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


@pytest.fixture
def session(tmp_path):
    """Minimal session stub the /bash handler is happy to consume."""
    folder_context = SimpleNamespace(folders=[str(tmp_path)])
    sess = SimpleNamespace(
        ui=None,  # quiet — handler skips printing when no ui is attached
        folder_context=folder_context,
        session_manager=SimpleNamespace(save_history=lambda *_a, **_kw: None),
        variables={},
    )
    return sess


def _bash(session, args: str):
    from mu.commands.shell import bash_cmd

    return bash_cmd(session, args, allow_prompt=False)


def test_bash_empty_command_shows_usage(session):
    res = _bash(session, "")
    assert not res.ok
    assert "Usage: /bash" in res.message


def test_bash_echo_round_trip(session):
    res = _bash(session, "echo hello-from-bash")
    assert res.ok
    assert res.data["exit_code"] == 0
    assert "hello-from-bash" in res.data["stdout"]


def test_bash_runs_in_workspace_cwd(session, tmp_path):
    (tmp_path / "marker.txt").write_text("present")
    res = _bash(session, "ls marker.txt")
    assert res.ok
    assert res.data["cwd"] == str(tmp_path)
    assert "marker.txt" in res.data["stdout"]


def test_bash_falls_back_to_process_cwd_without_workspace(monkeypatch, tmp_path):
    sess = SimpleNamespace(
        ui=None,
        folder_context=SimpleNamespace(folders=[]),
        session_manager=SimpleNamespace(save_history=lambda *_a, **_kw: None),
        variables={},
    )
    monkeypatch.chdir(tmp_path)
    res = _bash(sess, "pwd")
    assert res.ok
    assert res.data["stdout"].strip() == str(tmp_path)


def test_bash_nonzero_exit_marked_not_ok(session):
    res = _bash(session, "exit 7")
    assert not res.ok
    assert res.data["exit_code"] == 7
    assert not res.data["timed_out"]


def test_bash_captures_stderr(session):
    res = _bash(session, "echo oops >&2")
    assert res.ok  # exit code 0 still
    assert "oops" in res.data["stderr"]


def test_bash_timeout_returns_timed_out_envelope(session, monkeypatch):
    # Drop the timeout so the test stays cheap.
    import mu.commands.shell as shell_mod

    monkeypatch.setattr(shell_mod, "_BASH_TIMEOUT", 0.5)

    res = _bash(session, "sleep 5")
    assert not res.ok
    assert res.data["timed_out"] is True
    assert "timed out" in res.message.lower()


def test_bash_registered_with_aliases():
    from mu.commands import _REGISTRY

    assert "/bash" in _REGISTRY
    assert "/sh" in _REGISTRY
    assert "/!" in _REGISTRY
    # All three names point to the same handler.
    assert _REGISTRY["/bash"].handler is _REGISTRY["/sh"].handler
    assert _REGISTRY["/bash"].handler is _REGISTRY["/!"].handler
