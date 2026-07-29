"""Regression tests for web GUI shell connectivity fixes.

Verifies the four root causes of shell panel connectivity failures:

1. **x-effect infinite loop** — ``shell_panel.html`` used ``x-effect`` which
   re-fires on *any* reactive dependency change.  ``load()`` mutates reactive
   state (output, error, containerName, connecting, connected) → x-effect
   re-fires → ``load()`` again → disconnect socket, fetch, reconnect → state
   changes → re-fire.  INFINITE RECONNECT LOOP.

   Fix: replaced ``x-effect`` with ``x-init="$watch(...)"`` which fires only
   on *value change* of ``$store.mode.active``.

2. **Double-load** — ``load()`` always disconnected + reconnected even if
   already connected.  No guard.

   Fix: ``_loaded`` boolean guard at top of ``load()``.

3. **Disconnect message spam** — ``onclose`` appended
   ``"[shell disconnected]"`` on *every* close, including intentional
   disconnects from ``disconnect()`` or panel-switch.

   Fix: ``_intentional_close`` flag.  ``disconnect()`` sets it before
   ``close()``.  ``onclose`` only shows message when flag is false.

4. **Race condition** — ``load()`` is async (fetches ``/api/sessions/active``).
   If triggered again during ``await``, second call disconnects socket just
   created, starts another ``load()``.

   Fix: ``_loaded`` guard (set synchronously before first ``await``) prevents
   re-entry.  ``disconnect()`` resets ``_loaded`` so a genuine reconnect works.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "mu" / "gui" / "static" / "js" / "app.js"
SHELL_PANEL = ROOT / "mu" / "gui" / "templates" / "fragments" / "shell_panel.html"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _shell_store_section(src: str) -> str:
    """Extract the Alpine.store("shell", { ... }) block from app.js."""
    start = src.index('Alpine.store("shell"')
    # Find the next Alpine.store( call which marks the start of the next store.
    next_store = src.find('Alpine.store(', start + 1)
    if next_store == -1:
        return src[start:start + 5000]
    return src[start:next_store]


# ── Fix 1: x-effect → x-init + $watch ──────────────────────────────────────


def test_shell_panel_does_not_use_x_effect():
    """x-effect must NOT be used to trigger load() — it causes infinite loop."""
    src = read(SHELL_PANEL)
    assert "x-effect" not in src, (
        "shell_panel.html must not use x-effect — it re-fires on every reactive "
        "dependency change, causing an infinite reconnect loop"
    )


def test_shell_panel_uses_x_init_with_watch():
    """x-init + $watch fires only on value change, not every dependency mutation."""
    src = read(SHELL_PANEL)
    assert "x-init" in src, "shell_panel.html must use x-init"
    assert "$watch" in src, "shell_panel.html must use $watch on $store.mode.active"
    assert "$store.mode.active" in src, "$watch must observe $store.mode.active"


def test_shell_panel_watch_calls_load_on_shell():
    """$watch callback must call load() when mode becomes 'shell'."""
    src = read(SHELL_PANEL)
    assert "shell" in src, "watch must check for 'shell' mode"
    assert "$store.shell.load()" in src, "watch must call $store.shell.load()"


def test_shell_panel_watch_disconnects_on_leave():
    """$watch callback should disconnect when leaving shell mode."""
    src = read(SHELL_PANEL)
    assert "$store.shell.disconnect()" in src, (
        "watch should call disconnect() when leaving shell mode to clean up WS"
    )


# ── Fix 2: _loaded guard ───────────────────────────────────────────────────


def test_shell_store_has_loaded_guard():
    """load() must check _loaded and return early if already loaded."""
    src = read(APP_JS)
    assert "_loaded" in src, "shell store must have _loaded flag"
    assert "if (this._loaded) return" in src, (
        "load() must guard against double-load with early return"
    )


def test_shell_store_sets_loaded_before_await():
    """_loaded must be set synchronously before any await to prevent race."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    loaded_idx = section.index("this._loaded = true")
    fetch_idx = section.index("fetch(")
    assert loaded_idx < fetch_idx, (
        "_loaded must be set BEFORE the await fetch() call to prevent "
        "race condition where x-effect fires again during the await"
    )


def test_shell_store_disconnect_resets_loaded():
    """disconnect() must reset _loaded so a genuine reconnect works."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    # Find the disconnect() method DEFINITION, not the this.disconnect() call
    disc_idx = section.index("\n        disconnect()")
    disc_section = section[disc_idx:disc_idx + 300]
    assert "this._loaded = false" in disc_section, (
        "disconnect() must reset _loaded=false so reconnect is possible"
    )


# ── Fix 3: _intentional_close flag ─────────────────────────────────────────


def test_shell_store_has_intentional_close_flag():
    """Shell store must have _intentional_close flag."""
    src = read(APP_JS)
    assert "_intentional_close" in src, (
        "shell store must have _intentional_close flag"
    )


def test_shell_store_disconnect_sets_intentional_close():
    """disconnect() must set _intentional_close=true before close()."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    disc_idx = section.index("\n        disconnect()")
    disc_section = section[disc_idx:disc_idx + 300]
    assert "_intentional_close = true" in disc_section, (
        "disconnect() must set _intentional_close=true before closing socket"
    )
    close_idx = disc_section.index("this._socket.close()")
    flag_idx = disc_section.index("_intentional_close = true")
    assert flag_idx < close_idx, (
        "_intentional_close must be set BEFORE _socket.close() so onclose "
        "handler sees the flag"
    )


def test_shell_store_onclose_checks_intentional_close():
    """onclose must NOT append disconnect message when _intentional_close is true."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    onclose_idx = section.index("socket.onclose")
    onclose_section = section[onclose_idx:onclose_idx + 400]
    assert "_intentional_close" in onclose_section, (
        "onclose handler must check _intentional_close flag"
    )
    assert "if (!this._intentional_close)" in onclose_section, (
        "onclose must only append disconnect message when NOT intentional"
    )


def test_shell_store_connect_resets_intentional_close():
    """connect() must reset _intentional_close=false for fresh connection."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    connect_idx = section.index("\n        connect()")
    connect_section = section[connect_idx:connect_idx + 300]
    assert "_intentional_close = false" in connect_section, (
        "connect() must reset _intentional_close=false so a fresh connection "
        "doesn't inherit the flag from a previous intentional disconnect"
    )


# ── Fix 4: Race condition prevention ───────────────────────────────────────


def test_shell_store_load_guard_prevents_race():
    """_loaded guard prevents second load() from starting during async fetch."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    load_idx = section.index("async load()")
    load_section = section[load_idx:load_idx + 400]
    guard_idx = load_section.index("if (this._loaded) return")
    assert "this._loaded = true" in load_section, "guard flag must be set"
    set_idx = load_section.index("this._loaded = true")
    assert guard_idx < set_idx, "guard check before set"


def test_no_x_effect_in_shell_panel():
    """Double-check: no x-effect anywhere in shell panel template."""
    src = read(SHELL_PANEL)
    assert "x-effect" not in src, "x-effect must be completely removed"


# ── Structural integrity ───────────────────────────────────────────────────


def test_shell_store_load_still_fetches_active_session():
    """load() must still fetch /api/sessions/active to resolve container."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    assert "/api/sessions/active" in section, (
        "load() must still fetch /api/sessions/active"
    )
    assert "d.container" in section, "load() must read container from response"


def test_shell_store_connect_uses_websocket():
    """connect() must still use WebSocket to /api/containers/{name}/shell."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    assert "new WebSocket(" in section, "must use WebSocket"
    assert "/api/containers/" in section, "must hit container shell endpoint"


def test_shell_store_send_still_works():
    """send() must still send commands via the WebSocket."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    assert "this._socket.send(" in section, "send() must write to socket"
    assert "\\n" in section, "send() must append newline to command"


def test_shell_store_clear_still_works():
    """clear() must still clear output."""
    src = read(APP_JS)
    section = _shell_store_section(src)
    assert 'this.output = ""' in section, "clear() must reset output"


# ── Containers page modal comparison (should be unaffected) ─────────────────


def test_containers_page_shell_uses_plain_handler():
    """Containers page modal uses a plain click handler, NOT x-effect — this
    is why it works fine while the panel version was broken."""
    containers_js = ROOT / "mu" / "gui" / "static" / "js" / "containers.js"
    src = read(containers_js)
    assert "new WebSocket(" in src, "containers page must use WebSocket"
    assert "x-effect" not in src, "containers page must not use x-effect"
    # The containers page uses a function openShell(name) called from click
    assert "openShell" in src, "containers page must have openShell function"