"""Session picker surfaces active / loaded / working state (session_active_state).

GUI welcome.html must bind is_current/is_loaded/is_busy from /api/sessions
into row classes, a status pill, the aria-label and row ordering; app.css must
style those hooks (reduced-motion safe).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WELCOME_HTML = ROOT / "mu/gui/templates/fragments/welcome.html"
APP_CSS = ROOT / "mu/gui/static/css/wash/patterns/welcome.css"
SESSIONS_ROUTER = ROOT / "mu/gui/routers/sessions.py"


def _welcome() -> str:
    return WELCOME_HTML.read_text(encoding="utf-8")


def _css() -> str:
    return APP_CSS.read_text(encoding="utf-8")


def test_router_emits_state_flags_per_session_card():
    src = SESSIONS_ROUTER.read_text(encoding="utf-8")
    for key in ('"is_current"', '"is_loaded"', '"is_busy"'):
        assert key in src, key


def test_session_row_binds_state_classes():
    html = _welcome()
    assert "'is-current': !!s.is_current" in html
    assert "'is-loaded': !!s.is_loaded" in html
    assert "'is-busy': !!s.is_busy" in html


def test_session_row_renders_state_pill():
    html = _welcome()
    assert 'class="welcome-session-state"' in html
    assert 'x-text="sessionState(s)"' in html
    assert 'x-show="sessionState(s)"' in html
    assert "return 'Active'" in html
    assert "return 'Working'" in html
    assert "return 'Loaded'" in html


def test_session_row_aria_label_and_ordering():
    html = _welcome()
    assert 'x-for="s in sortedSessions()"' in html
    assert 'x-for="s in sessions"' not in html
    assert "sessionState(s).toLowerCase()" in html  # state surfaced to AT
    # current → busy → loaded → rest, stable within rank
    assert "s.is_current ? 0 : s.is_busy ? 1 : s.is_loaded ? 2 : 3" in html
    assert "(a[1]-b[1])" in html


def test_css_styles_state_hooks():
    css = _css()
    for selector in (
        ".welcome-session-row.is-loaded",
        ".welcome-session-row.is-current",
        ".welcome-session-state",
        ".welcome-session-state.is-current",
        ".welcome-session-state.is-busy",
        "@keyframes welcome-state-pulse",
    ):
        assert selector in css, selector


def test_css_busy_pulse_respects_reduced_motion():
    from wash_css_helpers import css, decls
    welcome = css("patterns/welcome.css")
    assert decls(welcome, ".welcome-session-state.is-busy", media="prefers-reduced-motion: reduce") == {"animation": "none"}
