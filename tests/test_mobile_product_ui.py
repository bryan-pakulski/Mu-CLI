"""Regression guards for the native mobile product visual system."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile" / "android"
TOKENS = MOBILE / "src" / "theme" / "tokens.ts"
APP = MOBILE / "App.tsx"
NAV = MOBILE / "src" / "navigation" / "AppNavigator.tsx"
ATMOSPHERE = MOBILE / "src" / "components" / "AtmosphericBackground.tsx"
HEADER = MOBILE / "src" / "components" / "ModernHeader.tsx"
CARD = MOBILE / "src" / "components" / "Card.tsx"
BUTTON = MOBILE / "src" / "components" / "Button.tsx"
INPUT = MOBILE / "src" / "components" / "Input.tsx"
SHEET = MOBILE / "src" / "components" / "ModernBottomSheet.tsx"
ADVANCED = MOBILE / "src" / "components" / "AdvancedSettingsSheet.tsx"
SESSIONS = MOBILE / "src" / "components" / "SwipeSessionsDrawer.tsx"
MODE_DRAWER = MOBILE / "src" / "components" / "ModeDrawer.tsx"
CHAT = MOBILE / "src" / "screens" / "ChatScreenProduct.tsx"
TRACE = MOBILE / "src" / "screens" / "SessionTraceScreenV2.tsx"
PACKAGE = MOBILE / "package.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_mobile_uses_same_alpine_environmental_palette_as_web():
    text = read(TOKENS)
    for value in (
        # planos palette (dark bg / light bg / dark accent / light accent)
        "#16161e", "#f6f8fa", "#7aa2f7", "#2f5bd3",
        "skyField", "glacierField", "sunriseField", "peachField",
        "alpineField", "snowField", "glassStrong", "hairline",
    ):
        assert value in text
    assert "#1A1814" not in text
    assert "#FAF8F5" not in text
    assert "#B0ACA0" not in text
    # The previous indigo palette is fully replaced by planos.
    assert "#0E0E10" not in text
    assert "#8B95FF" not in text


def test_mobile_atmosphere_is_smooth_colour_not_fake_landscape():
    app = read(APP)
    atmosphere = read(ATMOSPHERE)
    assert "<AtmosphericBackground>" in app
    assert "data:image/png;base64" not in atmosphere
    assert "backgroundColor: colors.bg" in atmosphere
    assert "no gradient, glow or blur" in atmosphere
    assert "resizeMode=\"stretch\"" not in atmosphere
    assert "styles.mountain" not in atmosphere
    assert "clipPath" not in atmosphere
    assert "polygon(" not in atmosphere
    # The atmosphere is a local embedded asset, not a remote wallpaper.
    assert "http://" not in atmosphere
    assert "https://" not in atmosphere


def test_mobile_navigation_uses_production_chat_and_glass_chrome():
    nav = read(NAV)
    assert "ChatScreenProduct" in nav
    assert "<ChatScreenProduct />" in nav
    assert "card: colors.glassStrong" in nav
    assert "border: colors.hairline" in nav
    assert "background: 'transparent'" in nav


def test_mobile_shared_surfaces_are_quiet_glass_not_legacy_cards():
    card = read(CARD)
    button = read(BUTTON)
    input_text = read(INPUT)
    sheet = read(SHEET)
    # planos panels: surface + 1px border + radius 10, no shadow.
    assert "backgroundColor: colors.surface" in card
    assert "borderColor: colors.border" in card
    assert "borderRadius: radii.panel" in card
    assert "shadowOpacity" not in card
    # planos buttons: primary = accent (or tone) fill, secondary = raised + border.
    assert "primary: { backgroundColor: toneColor || colors.accent," in button
    assert "secondary: { backgroundColor: colors.raised" in button
    # planos inputs sit on the page background with a 1px border.
    assert "backgroundColor: colors.bg" in input_text
    assert "borderColor: colors.border" in input_text
    # planos sheets: raised + 1px border, radius 10 top corners.
    assert "backgroundColor: colors.raised" in sheet
    assert "borderTopLeftRadius: radii.panel" in sheet
    assert "borderTopWidth: 1" in sheet


def test_mobile_header_matches_web_information_hierarchy():
    text = read(HEADER)
    assert 'accessibilityLabel="Open sessions"' in text
    assert 'accessibilityLabel="Open Trace Analyzer"' in text
    assert 'name="analytics-outline"' in text
    assert 'name="settings-outline"' in text
    # planos top bar: surface + 1px bottom border; settings groups are panels.
    assert "backgroundColor: colors.surface" in text
    assert "borderBottomColor: colors.border" in text
    assert "sectionBody: { borderWidth: 1, borderRadius: radii.panel" in text
    assert "borderBottomWidth: 1" in text
    assert "ellipsis-horizontal" not in text


def test_mobile_chat_is_flat_compact_and_single_pane():
    text = read(CHAT)
    assert "compact interim disclosures" in text
    assert "styles.interimHeader" in text
    assert "borderLeftWidth: 1" in text
    assert "backgroundColor: colors.glassStrong" in text
    # planos transcript bubbles: assistant squared top-left, user accent-tinted.
    assert "borderTopLeftRadius: 2" in text
    assert "backgroundColor: colors.accentSubtle, borderColor: colors.accent" in text
    assert "fontFamily: fonts.mono" in text
    assert "styles.composerUtilities" in text
    assert "styles.composerPane" in text
    assert "onSettingsPress" in text
    assert "settings-outline" in text
    assert "formatWorkedDuration" in text
    assert "Worked for ${minutes}m ${remainder}s" in text
    assert "borderRightWidth: 1" in text
    assert "borderRadius: 24" not in text


def test_mobile_settings_and_drawers_are_hairline_flat():
    settings = read(ADVANCED)
    sessions = read(SESSIONS)
    mode = read(MODE_DRAWER)
    # planos Settings: grouped panels with 1px borders, bordered value inputs.
    assert "groupList: { gap: spacing.md }" in settings
    assert "group: { borderWidth: 1, borderRadius: radii.panel" in settings
    assert "valueInput" in settings and "borderRadius: radii.control" in settings
    assert "changedBadge" not in settings
    assert "backgroundColor: colors.glassStrong" in sessions
    assert "borderRightWidth: 1" in sessions
    assert "borderRadius: 15" not in sessions
    assert "backgroundColor: colors.glassStrong" in mode
    assert "borderLeftWidth: 1" in mode


def test_mobile_trace_inherits_shared_glass_and_alpine_semantics():
    trace = read(TRACE)
    assert "<Card" in trace
    assert "color: colors.viz[0]" in trace
    assert "color: colors.viz[4]" in trace
    assert "color: summary.status === 'completed' ? colors.success : colors.warning" in trace
    assert "color: colors.viz[3]" in trace
    assert "#e0a040" not in trace.lower()


def test_mobile_visual_port_adds_no_new_rendering_dependency():
    package = read(PACKAGE)
    assert "expo-linear-gradient" not in package
    assert "expo-blur" not in package
