"""Static integration guards for the shared Mode OS surfaces."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "mu" / "gui" / "templates" / "fragments"
APP_JS = ROOT / "mu" / "gui" / "static" / "js" / "app.js"
APP_CSS = ROOT / "mu" / "gui" / "static" / "css" / "app.css"
MOBILE_COMPONENT = (
    ROOT / "mobile" / "android" / "src" / "components" / "ModeWorkspace.tsx"
)
MOBILE_SCREENS = ROOT / "mobile" / "android" / "src" / "screens"


def test_every_structured_mode_uses_the_shared_operating_header():
    for mode in ("research", "security", "debug", "loop", "feature", "teacher"):
        markup = (TEMPLATES / f"{mode}_panel.html").read_text(encoding="utf-8")
        assert 'include "fragments/mode_workspace_header.html"' in markup


def test_mode_workspace_exploration_state_survives_page_reload():
    script = APP_JS.read_text(encoding="utf-8")
    assert 'Alpine.store("modeWorkspace"' in script
    assert "mucli.mode-workspace.v1" in script
    assert "localStorage.setItem(this._storageKey" in script


def test_web_mode_os_has_explicit_light_and_dark_palette_tokens():
    css = APP_CSS.read_text(encoding="utf-8")
    assert ':root, html[data-theme="dark"]' in css
    assert 'html[data-theme="light"]' in css
    for token in (
        "--mode-research",
        "--mode-security",
        "--mode-debug",
        "--mode-loop",
        "--mode-feature",
        "--mode-teacher",
    ):
        assert css.count(token) >= 3
    assert "color-mix(in srgb, var(--bg" in css


def test_web_mode_workspace_uses_one_glass_plane_not_nested_cards_or_pills():
    css = APP_CSS.read_text(encoding="utf-8")
    markup = (TEMPLATES / "mode_workspace_header.html").read_text(encoding="utf-8")
    header_rule = css.split(".mode-os {", 1)[1].split(".mode-os-topline", 1)[0]
    metric_rule = css.split(".mode-os-metrics {", 1)[1].split(".mode-os-metric {", 1)[0]
    assert "background:" not in header_rule
    assert "box-shadow:" not in header_rule
    assert "border-radius:" not in header_rule
    assert "background:" not in metric_rule
    assert 'class="mode-os-status-dot"' not in markup
    assert '<dl class="mode-os-metrics">' in markup
    assert "border-radius: var(--radius-pill)" not in css[css.index(".mode-os {"):css.index(".panel-empty {")]


def test_mobile_workspace_uses_live_theme_hairlines_not_nested_surfaces():
    component = MOBILE_COMPONENT.read_text(encoding="utf-8")
    assert "const { colors" in component
    assert "colors.hairline" in component
    assert "colors.glass" not in component
    assert "colors.bgLift" not in component
    assert "colors.borderStrong" not in component
    assert "borderRadius: 999" not in component
    assert "backgroundColor: '#" not in component


def test_mobile_modes_and_tools_hubs_are_flat_lists_not_card_stacks():
    for name in ("ModesScreen.tsx", "WorkspaceScreen.tsx", "WorkspaceCategoryScreen.tsx"):
        source = (MOBILE_SCREENS / name).read_text(encoding="utf-8")
        assert "<Card" not in source, name
        assert "<Badge" not in source, name
        assert "borderRadius: 999" not in source, name
        assert "colors.hairline" in source, name


def test_every_registered_mobile_mode_explorer_uses_mode_os_header():
    for name in (
        "ResearchScreen.tsx",
        "SecurityScreen.tsx",
        "DebugScreen.tsx",
        "LoopScreen.tsx",
        "FeatureExplorerScreen.tsx",
        "TeacherScreen.tsx",
    ):
        source = (MOBILE_SCREENS / name).read_text(encoding="utf-8")
        assert "ModeWorkspaceHeader" in source, name
        assert "useModeWorkspaceView" in source, name
