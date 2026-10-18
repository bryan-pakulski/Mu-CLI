"""Tests for visualisations subsection in artifacts + panel sizing + mobile jitter fixes.

Verifies:
1. Web GUI artifacts panel has separate Visualizations + Files subsections.
2. Alpine artifacts store has visualizations() + files() filter helpers.
3. Mobile ArtifactStrip splits artifacts into VISUALIZATIONS + FILES sections.
4. Web GUI .registry-section uses flex:1 1 auto (not 50%) so few entries
   don't force the panel to be too small.
5. Mobile ChatScreen guards onContentSizeChange during user scroll to
   prevent bounce/jitter + uses maintainVisibleContentPosition.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_PANEL = ROOT / "mu/gui/templates/fragments/artifacts_panel.html"
APP_JS = ROOT / "mu/gui/static/js/app.js"
APP_CSS = ROOT / "mu/gui/static/css/wash/patterns/panels.css"  # Wash: panel rules left app.css
ARTIFACT_STRIP = ROOT / "mobile/android/src/components/ArtifactStrip.tsx"
CHAT_SCREEN = ROOT / "mobile/android/src/screens/ChatScreenProduct.tsx"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Web GUI: visualizations subsection ──────────────────────────────────


def test_artifacts_panel_has_visualizations_section():
    src = read(ARTIFACTS_PANEL)
    assert 'visualizations-registry' in src, "Missing visualizations-registry section"
    assert 'id="visualizations-title"' in src, "Missing visualizations title"
    assert "Visualizations" in src


def test_artifacts_panel_has_files_section():
    src = read(ARTIFACTS_PANEL)
    assert 'model-artifacts-registry' in src, "Missing model-artifacts-registry section"
    assert '<strong id="model-artifacts-title">Files</strong>' in src, (
        "Model artifacts section should be renamed to 'Files'"
    )


def test_artifacts_panel_visualizations_uses_view_url():
    src = read(ARTIFACTS_PANEL)
    assert 'a.view_url' in src, "Visualizations should link to view_url (open), not download_url"
    assert "$store.artifacts.visualizations()" in src


def test_artifacts_panel_files_uses_download_url():
    src = read(ARTIFACTS_PANEL)
    assert "$store.artifacts.files()" in src
    # Files section should not have the view_url link (only download)
    files_section = src.split('model-artifacts-registry')[1]
    assert 'a.view_url' not in files_section, (
        "Files section should not have view_url link"
    )


def test_artifacts_panel_visualizations_has_delete():
    src = read(ARTIFACTS_PANEL)
    viz_section = src.split('visualizations-registry')[1].split('model-artifacts-registry')[0]
    assert 'artifact-row-delete' in viz_section, "Visualizations section missing delete button"
    assert "Delete visualization" in viz_section


# ── Web GUI: Alpine store helpers ────────────────────────────────────────


def test_app_js_has_visualizations_helper():
    src = read(APP_JS)
    assert 'visualizations()' in src, "Missing visualizations() helper in artifacts store"
    assert 'a.kind === "visualization"' in src


def test_app_js_has_files_helper():
    src = read(APP_JS)
    assert 'files()' in src, "Missing files() helper in artifacts store"
    assert 'a.kind !== "visualization"' in src


# ── Web GUI: panel sizing fix ───────────────────────────────────────────


def test_registry_section_flex_auto():
    from wash_css_helpers import css, decls
    src = read(APP_CSS)
    assert decls(css("patterns/panels.css"), ".registry-section").get("flex") == "1 1 auto", (
        "registry-section should use flex:1 1 auto so few entries "
        "don't force the panel to be too small"
    )
    assert "flex:1 1 50%" not in src, (
        "Old flex:1 1 50% should be removed — it forced equal-height "
        "halves even with few entries"
    )


# ── Mobile: ArtifactStrip visualizations subsection ──────────────────────


def test_artifact_strip_has_visualizations_section():
    src = read(ARTIFACT_STRIP)
    assert ">Visualizations<" in src, "Missing Visualizations heading (sentence case, G§2.2)"
    assert "visualizations" in src
    assert "a.kind === 'visualization'" in src


def test_artifact_strip_has_files_section():
    src = read(ARTIFACT_STRIP)
    assert ">Files<" in src, "Missing Files heading (sentence case, G§2.2)"
    assert "a.kind !== 'visualization'" in src


def test_artifact_strip_viz_uses_view_url():
    src = read(ARTIFACT_STRIP)
    assert "artifactsApi.viewUrl" in src, "Visualizations should use viewUrl (open), not downloadUrl"
    assert "isViz" in src


def test_artifact_strip_viz_icon_differs_from_files():
    src = read(ARTIFACT_STRIP)
    # Wash line icons (G§4) replace the Ionicons names.
    assert 'name="bar-chart-3"' in src, "Visualizations should use chart icon"
    assert 'name="external-link"' in src, "Visualizations should use open icon for action"
    assert 'name="paperclip"' in src, "Files should use document icon"
    assert 'name="download"' in src, "Files should use download icon"


def test_artifact_strip_conditionally_renders_sections():
    src = read(ARTIFACT_STRIP)
    assert "visualizations.length > 0" in src, "Viz section should be conditional"
    assert "files.length > 0" in src, "Files section should be conditional"


# ── Mobile: ChatScreen jitter fix ────────────────────────────────────────


def test_chat_screen_guards_content_size_change_during_scroll():
    src = read(CHAT_SCREEN)
    # The live chat screen stops following output the moment the user drags,
    # and the content-size handler only scrolls while following.
    assert "onScrollBeginDrag={() => { followOutputRef.current = false; }}" in src, (
        "a user drag must stop auto-follow, otherwise the list fights the "
        "user's finger causing bounce/jitter"
    )
    assert "!followOutputRef.current || visualizationActiveRef.current) return;" in src


def test_chat_screen_has_maintain_visible_content_position():
    src = read(CHAT_SCREEN)
    assert "maintainVisibleContentPosition" in src, (
        "FlatList needs maintainVisibleContentPosition to prevent "
        "jump-on-insert when new messages arrive"
    )
    assert "minIndexForVisible" in src


def test_chat_screen_mvc_position_conditional():
    src = read(CHAT_SCREEN)
    # Should be conditional on messages.length > 0 to avoid issues with empty list
    assert "messages.length > 0" in src, (
        "maintainVisibleContentPosition should be conditional on having messages"
    )