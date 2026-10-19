"""Deleting a thread from the chat page: the server refuses while the thread's session is
loaded in memory, so the client unloads an idle, non-current thread first instead of
surfacing "is loaded — switch away from it first"."""

from pathlib import Path

APP = (Path(__file__).resolve().parents[1] / "mu" / "gui" / "static" / "js" / "app.js").read_text(encoding="utf-8")


def _remove_body() -> str:
    start = APP.index("        async remove(thread) {")
    return APP[start:APP.index("\n        },", start)]


def test_thread_delete_unloads_a_loaded_thread_first():
    body = _remove_body()
    unload = body.index("await sessions.unload(thread.session_name)")
    delete = body.index('{ method: "DELETE", cache: "no-store" }')
    assert unload < delete
    assert "sessions.isLoaded(thread.session_name)" in body
    # A failed unload stops the delete (the unload already explained why).
    assert "if (!(await sessions.unload(thread.session_name))) return;" in body


def test_thread_delete_never_unloads_the_current_or_a_running_thread():
    body = _remove_body()
    assert body.index("thread.session_name === this.currentName") < body.index("sessions.unload(")
    assert body.index("sessions.isBusy(thread.session_name)") < body.index("sessions.unload(")


def test_thread_delete_errors_read_cleanly():
    # The toast shows the server's sentence, not "Error: …".
    assert '(e && e.message) || String(e || "Thread delete failed")' in _remove_body()
