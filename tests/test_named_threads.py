"""Tests for OS-level thread naming (utils/threads.py).

Tests cover:
1. NamedThread sets Python-level name correctly
2. NamedThread sets OS-level name on Linux (reads /proc/self/task/<tid>/comm from within the thread)
3. set_os_thread_name() works on the main thread (Linux)
4. Names are truncated to 15 chars on Linux
5. NamedThread is a subclass of threading.Thread
6. set_os_thread_name returns False for empty strings
7. Falls back gracefully on mocked failures
"""
import ctypes
import ctypes.util
import os
import platform
import threading
import time

import pytest

from utils.threads import NamedThread, set_os_thread_name

IS_LINUX = platform.system() == "Linux"


def _get_os_tid():
    """Get the current thread's OS TID using syscall."""
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    gettid = libc.syscall
    gettid.argtypes = [ctypes.c_long]
    gettid.restype = ctypes.c_long
    SYS_gettid = 186  # x86_64
    return gettid(SYS_gettid)


class TestNamedThreadBasic:
    """Tests that work on all platforms (no OS-specific checks)."""

    def test_named_thread_sets_python_name(self):
        """NamedThread should set the Python-level thread name."""
        result = [None]

        def worker():
            result[0] = threading.current_thread().name

        t = NamedThread(target=worker, name="test-python-name")
        t.start()
        t.join(timeout=5)
        assert result[0] == "test-python-name"

    def test_named_thread_is_thread_subclass(self):
        """NamedThread should be a subclass of threading.Thread."""
        assert issubclass(NamedThread, threading.Thread)

    def test_named_thread_with_daemon(self):
        """NamedThread should support daemon parameter."""
        t = NamedThread(target=lambda: None, name="test-daemon", daemon=True)
        assert t.daemon is True

    def test_named_thread_default_name(self):
        """NamedThread without explicit name gets default Thread naming."""
        t = NamedThread(target=lambda: None)
        assert t.name.startswith("Thread-")

    def test_set_os_thread_name_returns_bool(self):
        """set_os_thread_name should return a boolean."""
        result = set_os_thread_name("test-return-type")
        assert isinstance(result, bool)

    def test_set_os_thread_name_empty_string(self):
        """set_os_thread_name with empty string should return False."""
        result = set_os_thread_name("")
        assert result is False


@pytest.mark.skipif(not IS_LINUX, reason="OS-level name checks require /proc on Linux")
class TestOSLevelNamesLinux:
    """Tests that verify OS-level thread naming on Linux using /proc."""

    def test_named_thread_sets_os_name(self):
        """NamedThread should set the OS-level thread name visible in /proc."""
        result = [None]

        def worker():
            tid = _get_os_tid()
            try:
                with open(f"/proc/self/task/{tid}/comm") as f:
                    result[0] = f.read().strip()
            except FileNotFoundError:
                result[0] = f"NOT_FOUND_{tid}"

        t = NamedThread(target=worker, name="os-name-test")
        t.start()
        t.join(timeout=5)
        assert result[0] == "os-name-test"

    def test_named_thread_truncates_long_name(self):
        """OS thread names on Linux are truncated to 15 characters."""
        long_name = "a-very-long-thread-name"
        result = [None]

        def worker():
            tid = _get_os_tid()
            try:
                with open(f"/proc/self/task/{tid}/comm") as f:
                    result[0] = f.read().strip()
            except FileNotFoundError:
                result[0] = f"NOT_FOUND_{tid}"

        t = NamedThread(target=worker, name=long_name)
        t.start()
        t.join(timeout=5)
        assert result[0] == long_name[:15]

    def test_set_os_thread_name_on_main_thread(self):
        """set_os_thread_name should work on the main thread."""
        original_name = None
        try:
            with open(f"/proc/self/task/{os.getpid()}/comm") as f:
                original_name = f.read().strip()

            result = set_os_thread_name("test-main-thrd")
            assert result is True

            with open(f"/proc/self/task/{os.getpid()}/comm") as f:
                new_name = f.read().strip()
            assert new_name == "test-main-thrd"
        finally:
            if original_name:
                set_os_thread_name(original_name)

    def test_named_thread_preserves_existing_name(self):
        """Already-named threads keep their Python name and set the same OS name."""
        result = [None]

        def worker():
            tid = _get_os_tid()
            try:
                with open(f"/proc/self/task/{tid}/comm") as f:
                    result[0] = f.read().strip()
            except FileNotFoundError:
                result[0] = None

        t = NamedThread(target=worker, name="subagent-watchdog")
        t.start()
        t.join(timeout=5)
        assert result[0] == "subagent-watchdog"[:15]


class TestGracefulFallback:
    """Tests that ensure graceful fallback on unsupported scenarios."""

    def test_set_os_thread_name_with_mock_failure(self, monkeypatch):
        """set_os_thread_name should return False when pthread calls fail."""
        import utils.threads
        monkeypatch.setattr(utils.threads, "_PTHREAD_LIB", False)
        monkeypatch.setattr(utils.threads, "_PTHREAD_SETNAME_NP", None)

        result = set_os_thread_name("should-fail")
        assert result is False