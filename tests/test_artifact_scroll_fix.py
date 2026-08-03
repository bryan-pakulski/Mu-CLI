"""Regression tests for artifact panel scrollability fix.

Bug: The artifacts panel was NOT scrollable because `.app.has-panel .mode-panel`
sets `display:block` at specificity (0,2,0), which overrode
`.artifacts-panel { display:flex }` at specificity (0,1,0). This broke the
flexbox column layout — `.unified-registry-body` flex:1 + min-height:0 had no
effect, so `.registry-section-list` overflow:auto had no bounded height → no
scroll. Shell + files panels already had `.app.has-panel .mode-panel.<name>`
{ display:flex }` overrides; artifacts panel was missing one.

Fix: Added `.app.has-panel .mode-panel.artifacts-panel { display:flex; }` before
the `.artifacts-panel` base rule, matching the shell-panel and files-panel
pattern.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "mu" / "gui" / "static" / "css" / "app.css"
HTML = ROOT / "mu" / "gui" / "templates" / "fragments" / "artifacts_panel.html"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── CSS fix ──────────────────────────────────────────────────────────────────


def test_artifacts_panel_has_display_flex_override():
    """The high-specificity display:flex override must exist for artifacts panel.

    This mirrors the pattern already used by shell-panel and files-panel:
    `.app.has-panel .mode-panel.<name> { display:flex; }` at specificity
    (0,3,0) overrides the generic `.app.has-panel .mode-panel { display:block; }`
    at (0,2,0).
    """
    src = read(CSS)
    assert ".app.has-panel .mode-panel.artifacts-panel" in src, (
        "Missing high-specificity display:flex override for artifacts panel"
    )


def test_artifacts_panel_display_flex_before_base_rule():
    """The override must appear before the base .artifacts-panel rule so it
    wins on equal specificity (last-wins). Actually specificity is higher,
    but placement before keeps it readable and matches shell/files pattern."""
    src = read(CSS)
    override_pos = src.find(".app.has-panel .mode-panel.artifacts-panel")
    base_pos = src.find(".artifacts-panel { display:flex")
    assert override_pos != -1, "Override rule not found"
    assert base_pos != -1, "Base .artifacts-panel rule not found"
    assert override_pos < base_pos, (
        "Override should appear before base .artifacts-panel rule"
    )


def test_mode_panel_display_block_does_not_win_over_artifacts():
    """Verify the generic mode-panel display:block rule exists (the culprit)
    and that the artifacts override has higher specificity."""
    src = read(CSS)
    # The culprit rule that was winning
    assert ".app.has-panel .mode-panel { display: block; }" in src or \
           ".app.has-panel .mode-panel { display:block; }" in src, (
        "Generic mode-panel display:block rule not found (needed to confirm "
        "specificity conflict)"
    )
    # The fix: higher specificity
    assert ".app.has-panel .mode-panel.artifacts-panel { display:flex; }" in src, (
        "Artifacts panel display:flex override not found or wrong syntax"
    )


def test_artifacts_panel_overflow_hidden_present():
    """The .artifacts-panel overflow:hidden rule must be present — it
    overrides .mode-panel overflow-y:auto so only the inner list scrolls."""
    src = read(CSS)
    assert ".artifacts-panel { overflow:hidden; }" in src, (
        "artifacts-panel overflow:hidden not found"
    )


def test_registry_section_list_overflow_auto():
    """The inner scroll container must have overflow:auto."""
    src = read(CSS)
    assert ".registry-section-list { flex:1; min-height:0; overflow:auto;" in src, (
        "registry-section-list overflow:auto not found"
    )


def test_unified_registry_body_flex_min_height_zero():
    """The unified-registry-body must have flex:1 + min-height:0 so it
    fills the flex column and constrains the inner scroll."""
    src = read(CSS)
    assert ".unified-registry-body" in src
    body_rule_start = src.index(".unified-registry-body")
    body_rule = src[body_rule_start:body_rule_start + 200]
    assert "flex:1" in body_rule, "unified-registry-body missing flex:1"
    assert "min-height:0" in body_rule, "unified-registry-body missing min-height:0"


# ── Pattern consistency with other panels ────────────────────────────────────


def test_shell_panel_has_same_pattern():
    """Shell panel already uses the same override pattern — confirms the
    pattern is established and artifacts now matches."""
    src = read(CSS)
    assert ".app.has-panel .mode-panel.shell-panel { display:flex; }" in src, (
        "Shell panel override not found (expected — it's the pattern we copied)"
    )


def test_files_panel_has_same_pattern():
    """Files panel also uses the same override pattern."""
    src = read(CSS)
    assert ".app.has-panel .mode-panel.files-panel" in src, (
        "Files panel override not found (expected — it's the established pattern)"
    )
    assert "display: flex" in src[src.index(".app.has-panel .mode-panel.files-panel"):src.index(".app.has-panel .mode-panel.files-panel")+80], (
        "Files panel override must set display:flex"
    )


# ── HTML template structure ───────────────────────────────────────────────────


def test_artifacts_panel_html_uses_correct_classes():
    """The HTML template must use mode-panel + artifacts-panel classes so
    the CSS override applies."""
    src = read(HTML)
    assert "mode-panel artifacts-panel" in src, (
        "artifacts_panel.html must use both mode-panel and artifacts-panel classes"
    )


def test_artifacts_panel_html_has_data_mode():
    """The panel must have data-mode='artifacts' for the mode-switch logic."""
    src = read(HTML)
    assert 'data-mode="artifacts"' in src, (
        "artifacts_panel.html missing data-mode='artifacts'"
    )


def test_artifacts_panel_html_has_unified_registry_body():
    """The unified-registry-body div must exist in the template."""
    src = read(HTML)
    assert "unified-registry-body" in src, (
        "unified-registry-body class not found in artifacts_panel.html"
    )


def test_artifacts_panel_html_has_registry_section_list():
    """The registry-section-list div must exist in the template."""
    src = read(HTML)
    assert "registry-section-list" in src, (
        "registry-section-list class not found in artifacts_panel.html"
    )