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
        "#0D1219", "#EDF3F8", "#8BA9C6", "#6286A8",
        "skyField", "glacierField", "sunriseField", "peachField",
        "alpineField", "snowField", "glassStrong", "hairline",
    ):
        assert value in text
    assert "#1A1814" not in text
    assert "#FAF8F5" not in text
    assert "#B0ACA0" not in text


def test_mobile_atmosphere_is_smooth_colour_not_fake_landscape():
    app = read(APP)
    atmosphere = read(ATMOSPHERE)
    assert "<AtmosphericBackground>" in app
    assert "data:image/png;base64" in atmosphere
    assert "smooth atmospheric colour/light falloff" in atmosphere
    assert "no scenery or geometric" in atmosphere
    assert "resizeMode=\"stretch\"" in atmosphere
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
    assert "backgroundColor: colors.glass" in card
    assert "borderColor: colors.hairline" in card
    assert "shadowOpacity: 0.04" in card
    assert "primary: { backgroundColor: colors.accentStrong" in button
    assert "secondary: { backgroundColor: 'transparent'" in button
    assert "backgroundColor: colors.glass" in input_text
    assert "backgroundColor: colors.glassStrong" in sheet
    assert "borderTopWidth: StyleSheet.hairlineWidth" in sheet


def test_mobile_header_matches_web_information_hierarchy():
    text = read(HEADER)
    assert 'accessibilityLabel="Open sessions"' in text
    assert 'accessibilityLabel="Open Trace Analyzer"' in text
    assert 'name="analytics-outline"' in text
    assert 'name="settings-outline"' in text
    assert "backgroundColor: colors.glass" in text
    assert "borderBottomColor: colors.hairline" in text
    assert "sectionBody: { borderTopWidth: StyleSheet.hairlineWidth }" in text
    assert "borderBottomWidth: StyleSheet.hairlineWidth" in text
    assert "ellipsis-horizontal" not in text


def test_mobile_chat_is_flat_compact_and_single_pane():
    text = read(CHAT)
    assert "compact interim disclosures" in text
    assert "styles.interimHeader" in text
    assert "borderLeftWidth: StyleSheet.hairlineWidth" in text
    assert "backgroundColor: colors.glassStrong" in text
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
    assert "groupList: { borderTopWidth: StyleSheet.hairlineWidth }" in settings
    assert "group: { borderBottomWidth: StyleSheet.hairlineWidth }" in settings
    assert "valueInput" in settings and "borderBottomWidth: StyleSheet.hairlineWidth" in settings
    assert "changedBadge" not in settings
    assert "backgroundColor: colors.glassStrong" in sessions
    assert "borderRightWidth: StyleSheet.hairlineWidth" in sessions
    assert "borderRadius: 15" not in sessions
    assert "backgroundColor: colors.glassStrong" in mode
    assert "borderLeftWidth: StyleSheet.hairlineWidth" in mode


def test_mobile_trace_inherits_shared_glass_and_alpine_semantics():
    trace = read(TRACE)
    assert "<Card" in trace
    assert "color: colors.accent" in trace
    assert "color: colors.info" in trace
    assert "color: colors.success" in trace
    assert "color: colors.error" in trace
    assert "#e0a040" not in trace.lower()


def test_mobile_visual_port_adds_no_new_rendering_dependency():
    package = read(PACKAGE)
    assert "expo-linear-gradient" not in package
    assert "expo-blur" not in package
