"""Tests for the GUI Memory Map panel feature.

Mirrors tests/test_gui_history_panel.py's structure:
  - 'memory' is a GUI view panel (in GUI_VIEW_PANELS), NOT an agent mode
    (not in AGENTIC_MODES / AGENT_MODE_METADATA, not settable via POST /api/modes)
  - memory_panel.html fragment exists with required elements
  - index.html includes memory_panel.html
  - app.js contains Alpine.store('memory') with grid/layers/load
  - app.js panelModes array contains 'memory'
  - app.js routes the context_snapshot SSE event
  - app.css has the memory panel classes
  - build_memory_snapshot: dims, determinism, layer change detection
"""

import os

from utils.config import AGENTIC_MODES, AGENT_MODE_METADATA, GUI_VIEW_PANELS


# ============================================================ view-panel registration


def test_memory_not_an_agent_mode():
    # memory is a read-only view panel, not a real agent mode — it must
    # not be settable as agent_mode.
    assert "memory" not in AGENTIC_MODES
    assert "memory" not in AGENT_MODE_METADATA


def test_memory_in_gui_view_panels():
    names = [p["name"] for p in GUI_VIEW_PANELS]
    assert "memory" in names
    panel = next(p for p in GUI_VIEW_PANELS if p["name"] == "memory")
    assert "display_name" in panel
    assert "description" in panel
    assert isinstance(panel["display_name"], str)
    assert isinstance(panel["description"], str)


def test_memory_not_in_no_workspace_set():
    from mu.gui.routers import modes as modes_mod
    import inspect

    source = inspect.getsource(modes_mod)
    assert "_NO_WORKSPACE_NEEDED" in source
    # memory dropped from the no-workspace set when it stopped being a mode
    assert '"memory"' not in source


# ============================================================ panel fragment


PANEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "templates", "fragments", "memory_panel.html",
)


def test_memory_panel_html_exists():
    assert os.path.isfile(PANEL_PATH)


def test_memory_panel_has_mode_panel_aside():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert 'class="mode-panel' in content
    assert 'data-mode="memory"' in content


def test_memory_panel_has_canvas():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "<canvas" in content
    assert "$store.memory.bindCanvas" in content


def test_memory_panel_has_resolution_control():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$store.memory.resolution" in content


def test_memory_panel_has_legend():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$store.memory.layers" in content


# ============================================================ index.html inclusion


INDEX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "templates", "index.html",
)


def test_index_html_includes_memory_panel():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "memory_panel.html" in content


# ============================================================ app.js Alpine store


APP_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "static", "js", "app.js",
)


def test_app_js_has_alpine_memory_store():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert 'Alpine.store("memory"' in content


def test_app_js_memory_store_has_load_method():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "/api/memory/state" in content


def test_app_js_memory_store_has_render_method():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "render()" in content
    assert "fillRect" in content


def test_app_js_memory_store_has_apply_snapshot():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "applySnapshot" in content


def test_app_js_panel_modes_includes_memory():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert '"memory"' in content
    assert "panelModes" in content


def test_app_js_routes_context_snapshot_event():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert 'case "context_snapshot"' in content


# ============================================================ CSS


CSS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "static", "css", "app.css",
)


def test_css_has_memory_panel_classes():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    for cls in (
        ".memory-panel",
        ".memory-canvas-wrap",
        ".memory-canvas",
        ".memory-dividers",
        ".memory-divider",
        ".memory-legend",
        ".memory-legend-row",
    ):
        assert cls in content, f"CSS class {cls} not found in app.css"


# ============================================================ backend wiring


def test_memory_router_registered():
    import inspect
    from mu.gui import app as app_mod

    source = inspect.getsource(app_mod)
    assert "memory as memory_router" in source
    assert 'prefix="/api/memory"' in source


def test_memory_snapshot_hook_registered_idempotent():
    from mu.agent.hooks import default_registry
    from mu.gui.app import _register_memory_snapshot_hook

    _register_memory_snapshot_hook()
    _register_memory_snapshot_hook()  # second call must not duplicate
    names = [s.name for s in default_registry.list("pre_provider_call")]
    assert names.count("gui_memory_snapshot") == 1


# ============================================================ builder behavior


def _make_session():
    from mu.session.session import Session, SessionManager
    from providers.base import LLMProvider, ProviderResponse

    class _DummyProvider(LLMProvider):
        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            return ProviderResponse(
                text="ok", parts=[], input_tokens=0, output_tokens=0, total_tokens=0
            )

        def upload_file(self, file_path, mime_type):
            return None

    sm = SessionManager()
    return Session(_DummyProvider(), False, "you are a helpful assistant", sm)


def _add_user_turn(session, text):
    session.session_manager.history.append(
        {"role": "user", "parts": [{"type": "text", "text": text}]}
    )


def test_snapshot_empty_session_returns_grid_dims():
    from mu.gui.memory_snapshot import build_memory_snapshot

    session = _make_session()
    snap = build_memory_snapshot(session, cols=64, rows=64)
    assert snap["active"] is True
    assert snap["cols"] == 64
    assert snap["rows"] == 64
    assert len(snap["grid"]) == 64
    assert all(len(r) == 64 for r in snap["grid"])
    # Every layer is represented in the legend, even if empty.
    ids = [l["id"] for l in snap["layers"]]
    assert ids == ["L0", "L1", "L1B", "L2", "L3", "L4B", "L5"]


def test_snapshot_is_deterministic():
    from mu.gui.memory_snapshot import build_memory_snapshot

    session = _make_session()
    _add_user_turn(session, "hello world this is a test of the memory map")
    a = build_memory_snapshot(session, cols=48, rows=48)
    b = build_memory_snapshot(session, cols=48, rows=48)
    assert a["grid"] == b["grid"]


def test_snapshot_changed_history_changes_grid():
    from mu.gui.memory_snapshot import build_memory_snapshot

    session = _make_session()
    _add_user_turn(session, "alpha beta gamma delta epsilon zeta eta theta")
    before = build_memory_snapshot(session, cols=48, rows=48)

    # Add a second turn — L5 (history) content changes, so the grid must
    # differ somewhere. Other layers are unchanged.
    _add_user_turn(session, "iota kappa lambda mu nu xi omicron pi")
    after = build_memory_snapshot(session, cols=48, rows=48)

    assert before["grid"] != after["grid"]
    # At least one layer registered a token change (L5 should grow).
    before_by_id = {l["id"]: l["tokens"] for l in before["layers"]}
    after_by_id = {l["id"]: l["tokens"] for l in after["layers"]}
    assert after_by_id["L5"] > before_by_id["L5"]


def test_snapshot_clamps_resolution():
    from mu.gui.memory_snapshot import build_memory_snapshot

    session = _make_session()
    huge = build_memory_snapshot(session, cols=9999, rows=9999)
    assert huge["cols"] == 256
    assert huge["rows"] == 256
    tiny = build_memory_snapshot(session, cols=1, rows=1)
    assert tiny["cols"] == 16
    assert tiny["rows"] == 16


def test_snapshot_none_session_is_inactive():
    from mu.gui.memory_snapshot import build_memory_snapshot

    snap = build_memory_snapshot(None, cols=32, rows=32)
    assert snap["active"] is False
    assert len(snap["grid"]) == 32
    # Empty cells are 0 (int), not None — the grid is an int heatmap now.
    assert all(c == 0 for r in snap["grid"] for c in r)


def test_snapshot_layers_carry_hue_and_change_count():
    from mu.gui.memory_snapshot import build_memory_snapshot, LAYER_HUES

    session = _make_session()
    _add_user_turn(session, "a b c d e f g h i j k l m n o p")
    snap = build_memory_snapshot(session, cols=48, rows=48)
    for l in snap["layers"]:
        assert "hue" in l
        assert "change_count" in l
        assert l["hue"] == LAYER_HUES.get(l["id"], 0)
        assert isinstance(l["change_count"], int)


def test_snapshot_grid_is_int_heatmap():
    from mu.gui.memory_snapshot import build_memory_snapshot

    session = _make_session()
    _add_user_turn(session, "a b c d e f g h i j k l m n o p")
    snap = build_memory_snapshot(session, cols=48, rows=48)
    flat = [c for r in snap["grid"] for c in r]
    # Every cell is an int in [0, 255]; 0 = empty, 1..255 = 1+heat.
    assert all(isinstance(c, int) for c in flat)
    assert all(0 <= c <= 255 for c in flat)


def test_snapshot_change_increments_heat():
    from mu.gui.memory_snapshot import build_memory_snapshot

    session = _make_session()
    _add_user_turn(session, "alpha beta gamma delta epsilon zeta eta theta")
    before = build_memory_snapshot(session, cols=48, rows=48)
    before_max = max(c for r in before["grid"] for c in r)
    before_total_changes = sum(l["change_count"] for l in before["layers"])

    # Add a second turn — L5 content shifts, so some canonical chunks now
    # hash differently and their per-chunk change counter ticks up.
    _add_user_turn(session, "iota kappa lambda mu nu xi omicron pi rho sigma")
    after = build_memory_snapshot(session, cols=48, rows=48)
    after_max = max(c for r in after["grid"] for c in r)
    after_total_changes = sum(l["change_count"] for l in after["layers"])

    assert after_total_changes > before_total_changes
    assert after_max > before_max


def test_hash_color_is_deterministic_and_hex():
    from mu.gui.memory_snapshot import _hash_color

    a = _hash_color("some chunk")
    b = _hash_color("some chunk")
    assert a == b
    assert a.startswith("#") and len(a) == 7
    # Different input → (very likely) different color.
    assert _hash_color("different chunk") != a