"""Tests for the GUI daemon control plane (``mu/gui/daemon.py``).

Regression target: ``mucli --gui-stop`` reported "no GUI server is running"
while a server was genuinely listening — the PID file was missing/stale (a
prior stop that deleted-then-missed, a direct ``--gui-foreground`` launch,
or the launcher parent dying before writing it) and stop relied solely on
the PID file. These tests pin the port→PID fallback that fixes it.
"""

import socket
import subprocess
import sys
import time
from io import StringIO, BytesIO
from unittest.mock import patch

import pytest

from mu.gui import daemon


@pytest.fixture
def ancestor_procfs(monkeypatch):
    """Two sibling namespaces reuse PID 8; only one belongs to this caller."""
    links = {
        "/proc/self": "100",
        "/proc/self/ns/pid": "pid:[1]",
        "/proc/100/ns/pid": "pid:[1]",
        "/proc/200/ns/pid": "pid:[2]",
        "/proc/300/ns/pid": "pid:[1]",
        "/proc/100/fd/3": "socket:[42]",
        "/proc/200/fd/3": "socket:[42]",
        "/proc/300/fd/3": "socket:[42]",
    }
    files = {
        "/proc/100/status": "NSpid:\t100\t7\n",
        "/proc/200/status": "NSpid:\t200\t8\n",
        "/proc/300/status": "NSpid:\t300\t8\n",
        "/proc/300/cmdline": b"python\0mucli.py\0--gui-foreground\0",
    }

    def readlink(path):
        if path not in links:
            raise FileNotFoundError(path)
        return links[path]

    def read_file(path, mode="r"):
        if path not in files:
            raise FileNotFoundError(path)
        return BytesIO(files[path]) if "b" in mode else StringIO(files[path])

    def listdir(path):
        if path == "/proc":
            return ["self", "100", "200", "300"]
        if path in {"/proc/100/fd", "/proc/200/fd", "/proc/300/fd"}:
            return ["3"]
        raise FileNotFoundError(path)

    monkeypatch.setattr(daemon.os, "getpid", lambda: 7)
    monkeypatch.setattr(daemon.os, "readlink", readlink)
    monkeypatch.setattr(daemon.os, "listdir", listdir)
    # Patch only the daemon module's open, leaving pytest's own I/O intact.
    monkeypatch.setattr(daemon, "open", read_file, raising=False)
    return links, files


def test_procfs_pid_maps_only_the_callers_namespace(ancestor_procfs):
    assert daemon._procfs_pid(8) == 300
    assert daemon._procfs_pid(9) is None
    assert daemon._procfs_pid(0) is None


def test_pid_for_port_maps_pids_and_ignores_self_and_siblings(ancestor_procfs, monkeypatch):
    monkeypatch.setattr(daemon, "_listener_inodes", lambda port: {"42"})
    assert daemon.pid_for_port(30311) == 8


def test_cmdline_guard_reads_the_mapped_process(ancestor_procfs):
    _, files = ancestor_procfs
    assert daemon._cmdline_is_mucli(8) is True
    files["/proc/300/cmdline"] = b"python\0unrelated.py\0"
    assert daemon._cmdline_is_mucli(8) is False
    assert daemon._cmdline_is_mucli(9) is None


@pytest.mark.parametrize("status", ["", "NSpid:\n", "NSpid: bad\n", "NSpid: 400 8\n", "NSpid: 300 0\n"])
def test_procfs_mapping_fails_closed_for_invalid_metadata(ancestor_procfs, status):
    _, files = ancestor_procfs
    files["/proc/300/status"] = status
    assert daemon._procfs_pid(8) is None


def test_procfs_mapping_fails_closed_for_unreadable_namespace(ancestor_procfs):
    links, _ = ancestor_procfs
    del links["/proc/300/ns/pid"]
    assert daemon._procfs_pid(8) is None


def test_procfs_pid_uses_direct_path_when_namespaces_match(monkeypatch):
    monkeypatch.setattr(daemon.os, "getpid", lambda: 7)
    monkeypatch.setattr(daemon.os, "readlink", lambda path: "7")
    with patch.object(daemon.os, "listdir") as scan:
        assert daemon._procfs_pid(8) == 8
    scan.assert_not_called()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_listener_inodes_finds_in_process_socket():
    """_listener_inodes returns the socket inode for a LISTEN on the port."""
    port = _free_port()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    try:
        inodes = daemon._listener_inodes(port)
        assert inodes, f"expected a listener inode on {port}, got {inodes}"
    finally:
        srv.close()


def test_pid_for_port_finds_subprocess_listener():
    """pid_for_port resolves the PID of a subprocess listening on the port
    (the PID-file-missing fallback path used by stop())."""
    port = _free_port()
    # Child: bind, signal ready, then just hold the socket open.
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import socket,sys,time\n"
                f"s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
                f"s.bind(('127.0.0.1',{port}));s.listen(1)\n"
                "sys.stdout.write('ready\\n');sys.stdout.flush()\n"
                "time.sleep(30)\n"
            ),
        ],
        stdout=subprocess.PIPE,
    )
    try:
        assert child.stdout.readline().strip() == b"ready"
        # Give /proc a moment to reflect the listening socket.
        pid = None
        for _ in range(20):
            pid = daemon.pid_for_port(port)
            if pid is not None:
                break
            time.sleep(0.1)
        assert pid == child.pid, f"expected child pid {child.pid}, got {pid}"
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()


def test_stop_falls_back_to_port_when_pid_file_missing(monkeypatch, tmp_path):
    """When the PID file is absent, stop() uses pid_for_port() to locate
    the server and SIGTERMs it, instead of giving up."""
    # Point the pid file at an empty dir so is_running() returns None.
    monkeypatch.setattr(daemon._config, "HISTORY_DIR", str(tmp_path), raising=False)
    assert daemon.is_running() is None

    # Pretend a server is listening on the port: pid_for_port returns a PID
    # that we can signal(0). Use our own process group isn't ideal; instead
    # stub pid_for_port to return a long-lived child and verify stop signals
    # it via os.kill by observing the child exits.
    # Round-27 F1: stop() now verifies the port-derived PID's cmdline
    # looks like mucli before signaling — the sleeper child is spawned
    # with a mucli-looking argv0 so it passes the identity guard, and a
    # companion test asserts a foreign process is NOT signaled.
    sleeper = subprocess.Popen(
        [
            "mucli-sleeper",
            "-c",
            "import time; print('ready', flush=True); time.sleep(30)",
        ],
        executable=sys.executable,
        stdout=subprocess.PIPE,
    )
    monkeypatch.setattr(daemon, "pid_for_port", lambda port: sleeper.pid)
    try:
        # Popen returns before the child finishes execve(). Waiting for an
        # explicit ready marker ensures /proc/<pid>/cmdline contains the
        # mucli-looking argv rather than the transient parent command line.
        assert sleeper.stdout is not None
        assert sleeper.stdout.readline().strip() == b"ready"
        ok, msg = daemon.stop(port=30311, timeout=5.0)
        assert ok, f"stop should succeed via port fallback, msg={msg}"
        assert "port 30311" in msg
        try:
            sleeper.wait(timeout=5)
            exited = True
        except subprocess.TimeoutExpired:
            exited = False
        assert exited, "stop() should have SIGTERM'd the port-discovered PID"
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sleeper.kill()
        if sleeper.stdout is not None:
            sleeper.stdout.close()


def test_stop_returns_false_when_nothing_running(monkeypatch, tmp_path):
    """No PID file AND nothing on the port → the original 'no GUI server
    is running' message (not a regression of the honest-not-running case)."""
    monkeypatch.setattr(daemon._config, "HISTORY_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(daemon, "pid_for_port", lambda port: None)
    ok, msg = daemon.stop(port=9999)
    assert ok is False
    assert "no GUI server is running" in msg

def test_stop_port_fallback_refuses_foreign_process(monkeypatch, tmp_path):
    """Round-27 F1: a port-derived PID whose cmdline does NOT look like
    mucli (foreign process holding the port, or PID reuse) must NOT be
    signaled — stop() refuses instead."""
    monkeypatch.setattr(daemon._config, "HISTORY_DIR", str(tmp_path), raising=False)
    assert daemon.is_running() is None

    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(daemon, "pid_for_port", lambda port: foreign.pid)
    try:
        ok, msg = daemon.stop(port=30311, timeout=1.0)
        assert not ok, "stop must refuse a foreign port holder"
        assert "no GUI server" in msg
        # Foreign process was NOT signaled — still alive before cleanup.
        assert foreign.poll() is None
    finally:
        foreign.terminate()
        try:
            foreign.wait(timeout=5)
        except subprocess.TimeoutExpired:
            foreign.kill()
