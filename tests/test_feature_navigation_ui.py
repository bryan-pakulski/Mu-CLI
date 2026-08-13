"""Regression guards for Feature Mode's single-surface navigation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PANEL = ROOT / "mu" / "gui" / "templates" / "fragments" / "feature_panel.html"
SCRIPT = ROOT / "mu" / "gui" / "static" / "js" / "app.js"
STYLES = ROOT / "mu" / "gui" / "static" / "css" / "app.css"
MOBILE = (
    ROOT
    / "mobile"
    / "android"
    / "src"
    / "screens"
    / "FeatureExplorerScreen.tsx"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_feature_browser_and_detail_are_mutually_exclusive_dom_views():
    panel = read(PANEL)

    assert 'x-if="$store.feature.isBrowserView()"' in panel
    assert 'x-if="$store.feature.isDetailView()"' in panel
    assert "feature-browse-overlay" not in panel
    assert "--bg-elevated" not in panel
    assert "Exactly one feature surface is mounted at a time" in panel


def test_archived_preview_closes_browser_and_rejects_stale_responses():
    script = read(SCRIPT)

    assert "navigationRevision: 0" in script
    assert "const navigationId = ++this.navigationRevision" in script
    assert "if (navigationId !== this.navigationRevision) return" in script
    assert "this.showFeatureBrowser = false" in script
    assert "await this.load({ forcePlan: true })" in script


def test_feature_plan_dom_keys_include_the_plan_identity():
    panel = read(PANEL)
    script = read(SCRIPT)

    assert "phaseRenderKey(phase)" in script
    assert "taskRenderKey(phase, task)" in script
    assert ':key="$store.feature.phaseRenderKey(phase)"' in panel
    assert ':key="$store.feature.taskRenderKey(phase, task)"' in panel


def test_feature_library_surface_uses_real_light_dark_theme_tokens():
    panel = read(PANEL)
    styles = read(STYLES)

    assert "--bg-elevated" not in panel
    assert ".feature-browser" in styles
    assert "var(--bg-lift)" in styles
    assert "var(--mode-feature)" in styles
    assert "color-mix(in srgb" in styles


def test_mobile_feature_navigation_ignores_late_preview_responses():
    source = read(MOBILE)

    assert "requestEpochRef" in source
    assert "requestId !== requestEpochRef.current" in source
    assert "requestEpochRef.current += 1" in source
    assert "key={`${selectedPlan.feature_id}:${phase.id}`}" in source
