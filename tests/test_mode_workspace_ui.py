"""Static integration guards for the shared Mode OS surfaces."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "mu" / "gui" / "templates" / "fragments"
APP_JS = ROOT / "mu" / "gui" / "static" / "js" / "app.js"
APP_CSS = ROOT / "mu" / "gui" / "static" / "css" / "wash" / "patterns" / "panels.css"
BASE_CSS = ROOT / "mu" / "gui" / "static" / "css" / "wash" / "base.css"
WASH_TOKENS_CSS = ROOT / "mu" / "gui" / "static" / "css" / "wash" / "tokens.css"
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
    base = BASE_CSS.read_text(encoding="utf-8")
    # Each mode keeps its own pigment in both themes: the generated Wash
    # tokens define the per-theme mode steps, and any element carrying
    # data-mode-tint reads them through the --w-mode-* channels in base.css.
    # Panel rules never redefine a mode colour.
    wash = WASH_TOKENS_CSS.read_text(encoding="utf-8")
    for selector in ('[data-theme="paper"]', '[data-theme="dusk"]', '[data-theme="light"]', '[data-theme="dark"]'):
        assert selector in wash
    for mode in ("research", "security", "debug", "loop", "feature", "teacher"):
        assert f"--wash-mode-{mode}-ink:" in wash
        assert f'[data-mode-tint="{mode}"] {{ --w-mode-wash: var(--wash-mode-{mode}-wash); --w-mode-ink: var(--wash-mode-{mode}-ink);' in base
        assert f"--mode-{mode}:" not in css and f"--wash-mode-{mode}-ink:" not in css
    assert re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css) is None

def test_web_mode_workspace_uses_one_glass_plane_not_nested_cards_or_pills():
    from wash_css_helpers import css, decls
    panels = css("patterns/panels.css")
    markup = (TEMPLATES / "mode_workspace_header.html").read_text(encoding="utf-8")
    for selector in (".mode-os", ".mode-panel .mode-os", ".mode-os-metrics"):
        rule = decls(panels, selector)
        assert "background" not in rule, selector
        assert "box-shadow" not in rule, selector
        assert "border-radius" not in rule, selector
    assert 'class="mode-os-status-dot"' not in markup
    assert '<dl class="mode-os-metrics">' in markup
    # Metrics are flat cells separated by hairlines, not pills.
    metric = decls(panels, ".mode-os-metric")
    assert metric.get("border-radius") == "0" and metric.get("background") == "none"

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
