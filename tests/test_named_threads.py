"""Tests for OS-level thread naming (utils/threads.py).

Tests cover:
1. NamedThread sets Python-level name correctly
2. NamedThread sets OS-level name on Linux (reads /proc/thread-self/comm)
3. set_os_thread_name() works on the main thread (Linux)
4. Names are truncated to 15 UTF-8 bytes on Linux
5. NamedThread is a subclass of threading.Thread
6. set_os_thread_name returns False for empty strings
7. Falls back gracefully on mocked failures
"""
import platform
import threading

import pytest

from utils.threads import NamedThread, set_os_thread_name

IS_LINUX = platform.system() == "Linux"


def _os_thread_name():
    # Let procfs resolve the current thread. Hard-coded x86 syscall numbers
    # and process-local TIDs do not work on ARM or ancestor-mounted procfs.
    with open("/proc/thread-self/comm") as f:
        return f.read().strip()


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
            result[0] = _os_thread_name()

        t = NamedThread(target=worker, name="os-name-test")
        t.start()
        t.join(timeout=5)
        assert result[0] == "os-name-test"

    def test_named_thread_truncates_long_name(self):
        """OS thread names on Linux are truncated to 15 characters."""
        long_name = "a-very-long-thread-name"
        result = [None]

        def worker():
            result[0] = _os_thread_name()

        t = NamedThread(target=worker, name=long_name)
        t.start()
        t.join(timeout=5)
        assert result[0] == long_name[:15]

    def test_set_os_thread_name_on_main_thread(self):
        """set_os_thread_name should work on the main thread."""
        original_name = None
        try:
            original_name = _os_thread_name()

            result = set_os_thread_name("test-main-thrd")
            assert result is True

            new_name = _os_thread_name()
            assert new_name == "test-main-thrd"
        finally:
            if original_name:
                set_os_thread_name(original_name)

    def test_named_thread_preserves_existing_name(self):
        """Already-named threads keep their Python name and set the same OS name."""
        result = [None]

        def worker():
            result[0] = _os_thread_name()

        t = NamedThread(target=worker, name="subagent-watchdog")
        t.start()
        t.join(timeout=5)
        assert result[0] == "subagent-watchdog"[:15]

    @pytest.mark.parametrize("name,expected", [
        ("分析任务处理线程", "分析任务处"),
        ("mu-😀😀😀😀", "mu-😀😀😀"),
    ])
    def test_named_thread_truncates_utf8_without_splitting_characters(self, name, expected):
        result = [None]

        def worker():
            result[0] = _os_thread_name()

        t = NamedThread(target=worker, name=name)
        t.start()
        t.join(timeout=5)
        assert result[0] == expected
        assert len(result[0].encode("utf-8")) <= 15


class TestGracefulFallback:
    """Tests that ensure graceful fallback on unsupported scenarios."""

    def test_set_os_thread_name_with_mock_failure(self, monkeypatch):
        """set_os_thread_name should return False when pthread calls fail."""
        import utils.threads
        monkeypatch.setattr(utils.threads, "_PTHREAD_LIB", False)
        monkeypatch.setattr(utils.threads, "_PTHREAD_SETNAME_NP", None)

        result = set_os_thread_name("should-fail")
        assert result is False
