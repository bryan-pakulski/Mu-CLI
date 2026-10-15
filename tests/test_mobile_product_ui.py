"""Regression guards for the native mobile product visual system."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile" / "android"
TOKENS = MOBILE / "src" / "theme" / "tokens.ts"
APP = MOBILE / "App.tsx"
NAV = MOBILE / "src" / "navigation" / "AppNavigator.tsx"
ATMOSPHERE = MOBILE / "src" / "ui" / "WashBackground.tsx"
HEADER = MOBILE / "src" / "components" / "ModernHeader.tsx"
CARD = MOBILE / "src" / "ui" / "Card.tsx"
BUTTON = MOBILE / "src" / "ui" / "Button.tsx"
INPUT = MOBILE / "src" / "ui" / "Form.tsx"
SHEET = MOBILE / "src" / "ui" / "Sheet.tsx"
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
    generated = read(MOBILE / "src" / "theme" / "wash.generated.ts")
    # Wash: the palette is generated from the shared tokens.json; tokens.ts
    # only adapts it and declares no colour literal of its own.
    assert "from './wash.generated'" in text
    assert re.search(r"#[0-9A-Fa-f]{6}", text) is None
    for value in ('"canvas": "#F8F6F1"', '"canvas": "#16131B"', '"lagoon-ink": "#2A6870"', '"hairline"'):
        assert value in generated
    for text in (text, generated):
        assert "#16161e" not in text and "#7aa2f7" not in text
        assert "#1A1814" not in text
    assert "#FAF8F5" not in text
    assert "#B0ACA0" not in text
    # The previous indigo palette is fully replaced by planos.
    assert "#0E0E10" not in text
    assert "#8B95FF" not in text


def test_mobile_atmosphere_is_smooth_colour_not_fake_landscape():
    app = read(APP)
    atmosphere = read(ATMOSPHERE)
    # Wash: the app sits on the canvas; watercolour art is bundled WebP
    # placed per screen, dissolving into canvas, with a flat-wash fallback.
    assert "<AppCanvas>" in app and "backgroundColor: colors.canvas" in app
    assert "data:image/png;base64" not in atmosphere
    assert "backgroundColor: colors.canvas" in atmosphere
    assert "require('../../assets/wash/dawn-paper.webp')" in atmosphere
    assert 'stopColor={colors.canvas} stopOpacity={1}' in atmosphere
    assert "reduceDecoration" in atmosphere
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
    assert "card: colors.raised" in nav
    assert "border: colors.hairline" in nav
    assert "background: 'transparent'" in nav


def test_mobile_shared_surfaces_are_quiet_glass_not_legacy_cards():
    card = read(CARD)
    button = read(BUTTON)
    input_text = read(INPUT)
    sheet = read(SHEET)
    # Wash cards (G§9.3): surface + 1px hairline + radius-lg, no shadow.
    assert "backgroundColor: elevated ? colors.raised : colors.surface" in card
    assert "borderColor: colors.hairline" in card
    assert "borderRadius: radii.lg" in card
    assert "shadowOpacity" not in card
    # Wash buttons (G§9.1): primary = ink pill with on-ink label, secondary =
    # surface pill with a hairline-strong outline.
    assert "case 'primary': return { bg: colors.ink, border: colors.ink, fg: colors.onInk };" in button
    assert "bg: selected ? colors.accent.wash : colors.surface, border: selected ? colors.accent.pigment : colors.hairlineStrong" in button
    assert "borderRadius: radii.pill" in button
    # Wash text fields (G§9.5): surface with a control-border (>= 3:1).
    assert "backgroundColor: colors.surface" in input_text
    assert "colors.controlBorder" in input_text
    # Wash sheets (G§8.3): raised level-3 surface, radius-xl top corners.
    assert "elevation(3, colors, isDark)" in sheet
    assert "borderTopLeftRadius: radii.xl" in sheet
    assert "borderTopWidth: 1" in sheet


def test_mobile_header_matches_web_information_hierarchy():
    text = read(HEADER)
    assert 'accessibilityLabel="Open sessions"' in text
    # Wash icon buttons (always labelled): trace and settings.
    assert '<IconButton icon="activity" label="Open Trace Analyzer"' in text
    assert '<IconButton icon="settings" label="Open settings" onPress={onOpenSettings}' in text
    # Wash top bar (G§8.3): solid canvas with a 1px hairline bottom edge.
    assert "backgroundColor: colors.canvas, borderBottomColor: colors.hairline" in text
    assert "borderBottomWidth: 1" in text
    assert "ellipsis-horizontal" not in text
    # Settings is a real screen (G§8.3) whose groups are sunken cards (G§9.3).
    settings = read(MOBILE / "src" / "screens" / "SettingsScreen.tsx")
    assert '<Card variant="sunken"' in settings
    assert "<Segmented<ThemePreference>" in settings and "Reduce decoration" in settings


def test_mobile_chat_is_flat_compact_and_single_pane():
    text = read(CHAT)
    assert "compact interim disclosures" in text
    assert "styles.interimHeader" in text
    assert "borderLeftWidth: 1" in text
    assert "backgroundColor: colors.raised" in text
    # planos transcript bubbles: assistant squared top-left, user accent-tinted.
    assert "borderTopLeftRadius: 2" in text
    assert "backgroundColor: colors.accent.wash, borderColor: colors.accent.ink" in text
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
    assert "backgroundColor: colors.raised" in sessions
    assert "borderRightWidth: 1" in sessions
    assert "borderRadius: 15" not in sessions
    assert "backgroundColor: colors.raised" in mode
    assert "borderLeftWidth: 1" in mode


def test_mobile_trace_inherits_shared_glass_and_alpine_semantics():
    trace = read(TRACE)
    assert "<Card" in trace
    assert "color: colors.accent.ink" in trace
    assert "color: colors.running.ink" in trace
    assert "color: summary.status === 'completed' ? colors.ok.ink : colors.attention.ink" in trace
    assert "color: colors.danger.ink" in trace
    assert "#e0a040" not in trace.lower()


def test_mobile_visual_port_adds_no_new_rendering_dependency():
    package = read(PACKAGE)
    assert "expo-linear-gradient" not in package
    assert "expo-blur" not in package
