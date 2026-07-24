"""OS-level thread naming utilities.

Python's threading.Thread(name=...) only sets the Python-level name visible
via threading.current_thread().name.  OS-level tools (ps, top, htop) still
show "python".  This module provides:

- set_os_thread_name(name): Set the OS-level name of the *current* thread
  via pthread_setname_np (Linux/macOS) or SetThreadDescription (Windows).
  Falls back silently on failure.

- NamedThread: A threading.Thread subclass that automatically calls
  set_os_thread_name(self.name) in its run() method before delegating
  to the target function.  Drop-in replacement for threading.Thread.
"""

import ctypes
import ctypes.util
import logging
import platform
import threading

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
_SYSTEM = platform.system()  # "Linux", "Darwin", "Windows", ...

# Linux: pthread_setname_np(pthread_t, const char*) — 15-char limit (no trailing NUL)
# macOS: pthread_setname_np(const char*) — ~63 char limit
# Windows: SetThreadDescription(HANDLE, PCWSTR) — no practical limit

_PTHREAD_LIB = None
_PTHREAD_SETNAME_NP = None
_KERNEL32 = None
_SET_THREAD_DESCRIPTION = None


def _init_linux_macos():
    """Lazy-initialise the pthread library handle (Linux/macOS)."""
    global _PTHREAD_LIB, _PTHREAD_SETNAME_NP
    if _PTHREAD_LIB is not None:
        return
    lib_path = ctypes.util.find_library("pthread")
    if not lib_path:
        logger.debug("threads: libpthread not found")
        _PTHREAD_LIB = False
        return
    try:
        _PTHREAD_LIB = ctypes.CDLL(lib_path, use_errno=True)
        _PTHREAD_SETNAME_NP = _PTHREAD_LIB.pthread_setname_np
        # Linux variant: int pthread_setname_np(pthread_t, const char*)
        # macOS variant: int pthread_setname_np(const char*)
        if _SYSTEM == "Linux":
            _PTHREAD_SETNAME_NP.argtypes = [ctypes.c_ulong, ctypes.c_char_p]
            _PTHREAD_SETNAME_NP.restype = ctypes.c_int
        else:  # Darwin
            _PTHREAD_SETNAME_NP.argtypes = [ctypes.c_char_p]
            _PTHREAD_SETNAME_NP.restype = ctypes.c_int
    except (OSError, AttributeError):
        logger.debug("threads: failed to load pthread_setname_np")
        _PTHREAD_LIB = False


def _init_windows():
    """Lazy-initialise the kernel32 handle (Windows)."""
    global _KERNEL32, _SET_THREAD_DESCRIPTION
    if _KERNEL32 is not None:
        return
    try:
        _KERNEL32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        _SET_THREAD_DESCRIPTION = _KERNEL32.SetThreadDescription
        _SET_THREAD_DESCRIPTION.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        _SET_THREAD_DESCRIPTION.restype = ctypes.c_int
    except (OSError, AttributeError):
        logger.debug("threads: SetThreadDescription not available")
        _KERNEL32 = False


def set_os_thread_name(name: str) -> bool:
    """Set the OS-level name of the current thread.

    On Linux the name is truncated to 15 characters (the kernel limit).
    On macOS the limit is ~63 characters.  On Windows there is no
    practical limit.

    Returns True if the call succeeded (or was a no-op), False if it
    failed silently (never raises).
    """
    if not name:
        return False

    if _SYSTEM == "Linux":
        _init_linux_macos()
        if not _PTHREAD_LIB or not _PTHREAD_SETNAME_NP:
            return False
        # pthread_self() returns the calling thread's pthread_t
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        pthread_self = libc.pthread_self
        pthread_self.restype = ctypes.c_ulong
        pthread_self.argtypes = []
        tid = pthread_self()
        # 15-char limit including NUL → 15 usable chars
        truncated = name[:15].encode("utf-8", errors="replace")
        try:
            rc = _PTHREAD_SETNAME_NP(tid, truncated)
            if rc != 0:
                logger.debug("threads: pthread_setname_np returned %d for %r", rc, name)
                return False
            return True
        except Exception:
            logger.debug("threads: pthread_setname_np failed for %r", name, exc_info=True)
            return False

    elif _SYSTEM == "Darwin":
        _init_linux_macos()
        if not _PTHREAD_LIB or not _PTHREAD_SETNAME_NP:
            return False
        # macOS: pthread_setname_np(const char*) — sets name of current thread
        truncated = name[:63].encode("utf-8", errors="replace")
        try:
            rc = _PTHREAD_SETNAME_NP(truncated)
            if rc != 0:
                logger.debug("threads: pthread_setname_np returned %d for %r", rc, name)
                return False
            return True
        except Exception:
            logger.debug("threads: pthread_setname_np failed for %r", name, exc_info=True)
            return False

    elif _SYSTEM == "Windows":
        _init_windows()
        if not _KERNEL32 or not _SET_THREAD_DESCRIPTION:
            return False
        # GetCurrentThread() returns a pseudo-handle (-1)
        try:
            rc = _SET_THREAD_DESCRIPTION(ctypes.c_void_p(-1), name)
            return rc == 0  # S_OK = 0
        except Exception:
            logger.debug("threads: SetThreadDescription failed for %r", name, exc_info=True)
            return False

    else:
        # Unsupported platform — silent no-op
        return False


class NamedThread(threading.Thread):
    """A Thread subclass that sets the OS-level thread name before running.

    Usage::

        t = NamedThread(target=my_func, name="worker-1")
        t.start()

    The Python-level name (threading.current_thread().name) is set as usual
    via the ``name`` kwarg.  Additionally, the OS-level name visible in
    ``ps``, ``top``, ``htop`` etc. is set to the same value (truncated
    to 15 chars on Linux).
    """

    def run(self) -> None:  # type: ignore[override]
        """Run the target, setting the OS-level thread name first."""
        set_os_thread_name(self.name)
        super().run()