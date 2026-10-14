"""prompt_toolkit shell around ``JobsApp`` (full-screen, keyboard driven).

* Renders ``views.render`` into one window; redraws are throttled to ≤10/s
  (``min_redraw_interval``).
* Spinners advance on ONE shared 120 ms tick, only while something active is
  on screen.
* Live updates: a background thread drains the durable change stream
  (``collect_durable_changes``) every second and refetches only what changed;
  without a change hook it polls every 2 s. Activity polls every 5 s while
  visible.
* ``$EDITOR`` runs via ``run_in_terminal`` (plan edits, long prompts, PR body).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional

from .app import JobsApp
from .data import JobsData
from .theme import TICK_S, color_mode

_NAMED_KEYS = {
    "up": "up", "down": "down", "left": "left", "right": "right", "pageup": "pageup", "pagedown": "pagedown",
    "home": "home", "end": "end", "enter": "enter", "escape": "escape", "tab": "tab", "s-tab": "s-tab",
    "backspace": "backspace", "c-c": "c-c", "c-k": "c-k", "c-e": "c-e", "c-u": "c-u", "c-p": "c-p",
    "c-n": "c-n", "c-s": "c-s", "space": "space",
}


def spawn_editor(text: str, suffix: str = ".md") -> Optional[str]:
    """Open ``$VISUAL``/``$EDITOR`` (default ``vi``) on ``text``; None if it failed."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    fd, path = tempfile.mkstemp(prefix="mucli-job-", suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        code = subprocess.call(shlex.split(editor) + [path])
        if code != 0:
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def run_jobs_tui(*, data: Optional[JobsData] = None, start_job: Optional[str] = None) -> None:
    from prompt_toolkit.application import Application, run_in_terminal
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output.color_depth import ColorDepth

    from . import views
    from .primitives import to_pt

    app_state = JobsApp(data or JobsData())
    app_state.refresh()
    if start_job:
        app_state.open_job(start_job)
    mode = color_mode()
    lock = threading.RLock()
    holder: dict = {}

    def get_text():
        size = holder["app"].output.get_size()
        with lock:
            lines = views.render(app_state, size.columns, size.rows)
        return to_pt(lines, mode)

    def editor(text: str, suffix: str, done: Callable[[Optional[str]], None]) -> None:
        async def go():
            result = await run_in_terminal(lambda: spawn_editor(text, suffix), in_executor=False)
            with lock:
                done(result)
            holder["app"].invalidate()
        holder["app"].create_background_task(go())

    app_state.editor = editor
    # Setup jobs / doctor runs finish (and stream log lines) on background threads: repaint then.
    app_state.on_change = lambda: holder["app"].invalidate() if "app" in holder else None
    kb = KeyBindings()

    def dispatch(name: str, event) -> None:
        with lock:
            app_state.handle_key(name)
            should_exit = app_state.exit_requested
        if should_exit:
            event.app.exit()
        else:
            event.app.invalidate()

    for pt_key, name in _NAMED_KEYS.items():
        pt = {"enter": "c-m", "backspace": "c-h", "space": " "}.get(pt_key, pt_key)
        kb.add(pt, eager=True)(lambda event, name=name: dispatch(name, event))

    @kb.add(Keys.Any)
    def _(event):
        data_text = event.data or ""
        if data_text and data_text.isprintable():
            dispatch(data_text, event)

    control = FormattedTextControl(get_text, focusable=True, show_cursor=False)
    app = Application(
        layout=Layout(Window(control, always_hide_cursor=True, wrap_lines=False)),
        key_bindings=kb, full_screen=True, mouse_support=False, min_redraw_interval=0.1,
        color_depth=ColorDepth.TRUE_COLOR if mode == "truecolor" else (
            ColorDepth.MONOCHROME if mode == "none" else ColorDepth.DEPTH_4_BIT),
    )
    app.ttimeoutlen = 0.05
    app.timeoutlen = 0.3
    holder["app"] = app
    stop = threading.Event()

    def poller() -> None:
        last_full = time.monotonic()
        last_activity = 0.0
        while not stop.wait(1.0):
            try:
                changes = app_state.data.changes()
            except Exception:  # noqa: BLE001
                changes = None
            now = time.monotonic()
            changed = False
            with lock:
                try:
                    if changes is None:
                        if now - last_full >= 2.0:
                            app_state.refresh()
                            last_full = now
                            changed = True
                    else:
                        changed = app_state.apply_changes(changes)
                        if now - last_full >= 15.0:  # safety net for missed events
                            app_state.refresh(activity=False)
                            last_full = now
                            changed = True
                    if app_state.maybe_refresh_live_diff(now):
                        changed = True
                    if app_state.route["name"] == "activity" and now - last_activity >= 5.0:
                        app_state.refresh_activity()
                        last_activity = now
                        changed = True
                except Exception as exc:  # noqa: BLE001
                    app_state.flash(f"refresh failed: {exc}", "danger")
                    changed = True
            if changed:
                app.invalidate()

    async def ticker() -> None:
        while True:
            await asyncio.sleep(TICK_S)
            with lock:
                animate = views._animate(app_state)
                if animate:
                    app_state.tick += 1
                flash_live = app_state.flash_msg is not None
            if animate or flash_live:
                app.invalidate()

    thread = threading.Thread(target=poller, name="mucli-jobs-tui-poll", daemon=True)
    thread.start()

    def pre_run() -> None:
        app.create_background_task(ticker())

    try:
        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False
        app.run(pre_run=pre_run, in_thread=running)
    finally:
        stop.set()
