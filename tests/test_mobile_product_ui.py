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
GLASS = MOBILE / "src" / "ui" / "Glass.tsx"


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
        # The old canvas colour survives only as the μ app icon's ground (`mark-ground`).
        assert "#1A1814" not in text.replace('"mark-ground": "#1A1814"', "")
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
    # Wash glass (G§3.3, G§6.2): the navigator is transparent so the fixed
    # ambient wash shows through; screens sit on a level-1 glass stage and
    # the tab bar and tab headers are blurred glass chrome.
    assert "card: 'transparent'" in nav
    assert "border: colors.hairline" in nav
    assert "background: 'transparent'" in nav
    assert "contentStyle: stage" in nav and "glassStyle(1, colors, { flat: true, solid: reduceDecoration })" in nav
    assert "tabBarBackground: TabBarGlass" in nav and "headerBackground: HeaderGlass" in nav
    assert "setRouteArt(routeAmbient(" in nav


def test_mobile_shared_surfaces_are_quiet_glass_not_legacy_cards():
    card = read(CARD)
    button = read(BUTTON)
    input_text = read(INPUT)
    sheet = read(SHEET)
    glass = read(GLASS)
    # Wash cards (G§9.3, G§3.3): level-1 glass (level 2 when elevated) with a
    # light glass edge and radius-lg; the depth and the solid surface/hairline
    # fallback live in the one glassStyle helper, and no card draws a side rule.
    assert "glass.style(elevated ? 2 : 1), borderRadius: radii.lg" in card
    assert "borderLeftWidth: 4" not in card and "shadowOpacity" not in card
    assert "borderColor: g.edge, borderTopColor: g.border" in glass
    assert "return level === 1 ? elevation(1, colors) : elevation(level, colors);" in glass
    # Wash buttons (G§9.1): primary = ink pill with on-ink label, secondary =
    # surface pill with a hairline-strong outline.
    assert "case 'primary': return { bg: colors.ink, border: colors.ink, fg: colors.onInk };" in button
    assert "bg: selected ? colors.accent.wash : colors.surface, border: selected ? colors.accent.pigment : colors.hairlineStrong" in button
    assert "borderRadius: radii.pill" in button
    # Wash text fields (G§9.5, G§3.3): a glass-subtle inset (solid surface
    # with Reduce decoration) with a control-border (>= 3:1).
    assert "backgroundColor: glass.solid ? colors.surface : glass.style('subtle').backgroundColor" in input_text
    assert "colors.controlBorder" in input_text
    # Wash sheets (G§8.3, G§3.3): level-3 blurred glass chrome (solid raised
    # fallback), radius-xl top corners.
    assert "glassStyle(3, colors, { solid: reduceDecoration })" in sheet
    assert "<GlassBackdrop level={3}" in sheet
    assert "borderTopLeftRadius: radii.xl" in sheet
    assert "borderTopWidth: 1" in sheet


def test_mobile_header_matches_web_information_hierarchy():
    text = read(HEADER)
    assert 'accessibilityLabel="Open sessions"' in text
    # Wash icon buttons (always labelled): trace and settings.
    assert '<IconButton icon="activity" label="Open Trace Analyzer"' in text
    assert '<IconButton icon="settings" label="Open settings" onPress={onOpenSettings}' in text
    # Wash top bar (G§8.3, G§3.3): level-1 blurred glass chrome over the
    # ambient wash with a 1px neutral glass bottom edge (solid with Reduce
    # decoration).
    assert '<GlassChrome level={1} edges={false} testID="chat-header-glass"' in text
    assert "borderBottomColor: glass.edge" in text
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
    # Wash transcript (G§9.7): user = accent-wash bubble, 6px bottom-right
    # corner, <= 80% wide; assistant = no bubble, prose on canvas.
    assert "isUser ? { backgroundColor: colors.accent.wash } : null" in text
    assert "borderBottomRightRadius: 6" in text and "maxWidth: '80%'" in text
    assert "assistantMessage: { width: '100%', paddingTop: spacing.xs }" in text
    # Interim turns collapse to one "Worked · N steps" row; tool calls are ToolCards.
    assert "Worked · {count} step" in text
    assert "<ToolCard call={item.tool}" in text
    assert "styles.composerUtilities" in text
    assert "styles.composerPane" in text
    assert "onSettingsPress" in text
    assert 'label="Session settings"' in text
    assert "formatWorkedDuration" in text
    assert "Worked for ${minutes}m ${remainder}s" in text
    # One composer pill with a 44dp send.
    assert "sendButton: { width: 44, height: 44, borderRadius: radii.pill" in text
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
    # Drawers are side panels: level-1 glass chrome (G§3.3) with one neutral
    # 1px hairline edge; the active session row is a glass-strong pill, not an
    # accent fill or side bar (G§9.20).
    assert '<GlassBackdrop level={1} testID="sessions-drawer-glass"' in sessions
    assert "borderRightWidth: 1" in sessions and "borderRightColor: colors.hairline" in sessions
    assert "activeSessionName === session.name && glass.activePill" in sessions
    assert "borderRadius: 15" not in sessions
    assert '<GlassBackdrop level={1} testID="mode-drawer-glass"' in mode
    assert "borderLeftWidth: 1" in mode and "borderLeftColor: colors.hairline" in mode


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
    # expo-blur is the one rendering dependency the glass spec adds (spec §13,
    # G§3.3), and only the kit's chrome helper uses it: lists and cards stay
    # translucent fills, so scrolling never re-blurs per row.
    assert '"expo-blur"' in package
    users = sorted(p.relative_to(MOBILE).as_posix() for p in (MOBILE / "src").rglob("*.tsx") if "expo-blur" in read(p))
    assert users == ["src/ui/Glass.tsx"]
