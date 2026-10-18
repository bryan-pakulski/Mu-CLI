"""Wash CSS contract (spec §4.1, §8.1). static/css/ now holds only wash/.

The generated tokens.css is the only place colour literals may live. Wash
stylesheets use tokens, never `!important` outside utilities.css, only the
four breakpoints, one owner per component class, and only --wash-* custom
properties that the generator actually emits.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WASH = ROOT / "mu" / "gui" / "static" / "css" / "wash"
TOKENS = WASH / "tokens.css"
GENERATED = {TOKENS}
BREAKPOINTS = {"600", "840", "1200", "1600"}


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _wash_files() -> list[Path]:
    return sorted(p for p in WASH.rglob("*.css") if p not in GENERATED)


def _rel(path: Path) -> str:
    return str(path.relative_to(WASH))


def test_wash_tree_has_the_expected_shape():
    for name in ("wash.css", "base.css", "layout.css", "utilities.css", "tokens.css", "hljs-wash.css"):
        assert (WASH / name).is_file(), name
    for name in ("button", "chip", "card", "nav", "tabs", "form", "modal", "drawer", "menu",
                 "toast", "banner", "empty", "table", "progress", "stepper", "icon"):
        assert (WASH / "components" / f"{name}.css").is_file(), name
    wash = (WASH / "wash.css").read_text(encoding="utf-8")
    # Wash final pass (spec §4.4 W4, §7 P7): the legacy layer is retired.
    assert wash.startswith("@layer tokens, base, layout, components, patterns, utilities;")


def test_signature_components_are_layered_and_imported():
    # W3 signature components (spec §4.1) each own a file in components/ and
    # load through wash.css into the components layer.
    wash = (WASH / "wash.css").read_text(encoding="utf-8")
    for name in ("panel", "composer", "message", "markdown", "code", "toolcard", "approval",
                 "prompt", "diff", "meter", "palette", "session-list"):
        assert (WASH / "components" / f"{name}.css").is_file(), name
        assert f'@import url("components/{name}.css") layer(components);' in wash, name
    # The chat page bundle carries its pattern files in the patterns layer.
    chat = (WASH / "patterns" / "chat.css").read_text(encoding="utf-8")
    for name in ("panels", "settings", "welcome"):
        assert (WASH / "patterns" / f"{name}.css").is_file(), name
        assert f'@import url("{name}.css") layer(patterns);' in chat, name


def test_no_colour_literals_outside_generated_tokens():
    offenders = []
    for path in _wash_files():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(", text):
            offenders.append(f"{_rel(path)}: {match.group(0)}")
    assert not offenders, offenders


def test_no_important_outside_utilities():
    offenders = [
        _rel(path) for path in _wash_files()
        if path.name != "utilities.css" and "!important" in _strip_comments(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


def test_only_the_four_breakpoints():
    offenders = []
    for path in _wash_files():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for query in re.findall(r"@media([^{]*)\{", text):
            for value in re.findall(r"(\d+)px", query):
                if value not in BREAKPOINTS:
                    offenders.append(f"{_rel(path)}: {query.strip()}")
    assert not offenders, offenders


def _rule_preludes(text: str):
    """Yield the selector text of every style rule (skips @-rule preludes)."""
    text = _strip_comments(text)
    depth_stack = []
    start = 0
    for index, char in enumerate(text):
        if char == "{":
            prelude = text[start:index].strip()
            depth_stack.append(prelude)
            if not prelude.startswith("@") and not re.match(r"^(from|to|\d+%)", prelude):
                yield prelude
            start = index + 1
        elif char == "}":
            if depth_stack:
                depth_stack.pop()
            start = index + 1
        elif char == ";":
            start = index + 1


def _split_selector_list(prelude: str) -> list[str]:
    """Split on top-level commas only (keeps `:is(a, b)` together)."""
    parts, depth, current = [], 0, ""
    for char in prelude:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def test_no_component_class_is_defined_in_two_component_files():
    # A component file "defines" the first class of every complex selector it
    # styles (".w-card[data-state] .w-card__head" is owned by .w-card).
    owners: dict[str, set[str]] = {}
    for path in sorted((WASH / "components").glob("*.css")):
        for prelude in _rule_preludes(path.read_text(encoding="utf-8")):
            for selector in _split_selector_list(prelude):
                match = re.search(r"\.(-?[_a-zA-Z][\w-]*)", selector)
                if match:
                    owners.setdefault(match.group(1), set()).add(path.name)
    shared = {name: sorted(files) for name, files in owners.items() if len(files) > 1}
    assert not shared, shared


def test_component_classes_use_the_w_prefix():
    offenders = []
    for path in sorted((WASH / "components").glob("*.css")):
        for prelude in _rule_preludes(path.read_text(encoding="utf-8")):
            for name in re.findall(r"\.(-?[_a-zA-Z][\w-]*)", prelude):
                if not name.startswith("w-"):
                    offenders.append(f"{path.name}: .{name}")
    assert not offenders, offenders


def test_every_wash_custom_property_used_exists_in_tokens():
    defined = set(re.findall(r"(--wash-[\w-]+)\s*:", TOKENS.read_text(encoding="utf-8")))
    missing = []
    for path in _wash_files():
        for name in re.findall(r"var\(\s*(--wash-[\w-]+)", path.read_text(encoding="utf-8")):
            if name not in defined:
                missing.append(f"{_rel(path)}: {name}")
    assert not missing, missing


def test_no_stylesheet_outside_wash_carries_important():
    # Inside cascade layers `!important` reverses layer priority (spec §4.1);
    # there are no stylesheets outside wash/ left to carry one.
    css_root = ROOT / "mu" / "gui" / "static" / "css"
    offenders = [
        str(p.relative_to(css_root)) for p in sorted(css_root.rglob("*.css"))
        if WASH not in p.parents and "!important" in _strip_comments(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


def test_legacy_alias_tokens_are_retired():
    # Spec §7 P7: the W1 alias file (static/css/tokens.css: --accent, --surface,
    # --viz-*, --st-*, …) is gone; every consumer reads --wash-* directly.
    assert not (ROOT / "mu" / "gui" / "static" / "css" / "tokens.css").exists()
    generated = TOKENS.read_text(encoding="utf-8")
    for wash in ("--wash-accent-ink", "--wash-attention-ink", "--wash-running-ink", "--wash-layer-L0",
                 "--wash-layer-L5", "--wash-surface", "--wash-canvas", "--wash-ink"):
        assert f"{wash}:" in generated, wash


def test_every_page_links_wash_css_through_the_layout_only():
    templates = ROOT / "mu" / "gui" / "templates"
    layout = (templates / "_layout.html").read_text(encoding="utf-8")
    assert layout.count('rel="stylesheet"') == 1
    assert '<link rel="stylesheet" href="/static/css/wash/wash.css">' in layout
    for page in ("index.html", "work.html", "containers.html", "trace.html",
                 "job_trace.html", "model_costs.html", "design/gallery.html"):
        html = (templates / page).read_text(encoding="utf-8")
        links = re.findall(r'<link[^>]+rel="stylesheet"[^>]*>', html)
        assert len(links) <= 1, (page, links)
        assert all("/static/css/wash/patterns/" in link for link in links), (page, links)


# ------------------------------------------------------------ W4 page bundles
W4_BUNDLES = ("work.css", "containers.css", "trace.css", "trace-common.css", "job-trace.css", "model-costs.css")
RETIRED_PAGE_CSS = ("work.css", "work_management.css", "work_polish.css", "work_jira.css", "containers.css",
                    "containers-crystal.css", "trace.css", "job_trace.css", "model_costs.css", "route-product.css")


def test_w4_page_bundles_are_wash_rules_not_legacy_imports():
    # Spec W4: /work, /containers, /trace, /jobs/trace and /models/costs run on
    # Wash rules; their bundles import no legacy stylesheet into `legacy`.
    for name in W4_BUNDLES:
        text = _strip_comments((WASH / "patterns" / name).read_text(encoding="utf-8"))
        assert "layer(legacy)" not in text, name
        if name == "trace-common.css":
            continue  # a partial: the analyzer bundles import it into `patterns` (below)
        assert "@layer patterns" in text, name
        for imported in re.findall(r'@import url\("([^"]+)"\)', text):
            assert "/" not in imported and imported in W4_BUNDLES, (name, imported)


def test_retired_page_stylesheets_are_deleted():
    legacy = ROOT / "mu" / "gui" / "static" / "css"
    assert [name for name in RETIRED_PAGE_CSS if (legacy / name).exists()] == []
    # Nothing still references them (templates, JS, Wash CSS).
    gui = ROOT / "mu" / "gui"
    pattern = re.compile(r"/static/css/(?:" + "|".join(re.escape(n) for n in RETIRED_PAGE_CSS) + r")\b"
                         r"|\.\./\.\./(?:" + "|".join(re.escape(n) for n in RETIRED_PAGE_CSS) + r")\b")
    offenders = [str(p.relative_to(gui)) for ext in ("*.html", "*.js", "*.css") for p in gui.rglob(ext)
                 if pattern.search(p.read_text(encoding="utf-8"))]
    assert not offenders, offenders


def test_work_scripts_render_without_inline_styles():
    # Spec §4.3: the 18 inline style attributes in work/view.js and 10 in
    # work/rules.js are gone; proportional bars are SVG geometry instead.
    js = ROOT / "mu" / "gui" / "static" / "js"
    for path in (js / "work" / "view.js", js / "work" / "rules.js", js / "wash" / "diff.js"):
        text = path.read_text(encoding="utf-8")
        assert "style=" not in text and ".style." not in text, path.name


def test_shared_analyzer_rules_load_into_the_patterns_layer():
    for name in ("trace.css", "job-trace.css"):
        text = (WASH / "patterns" / name).read_text(encoding="utf-8")
        assert '@import url("trace-common.css") layer(patterns);' in text, name


# ------------------------------------------------------------ final pass: one Wash tree (spec §4.4 W4, §7 P7, §11)
CSS_ROOT = ROOT / "mu" / "gui" / "static" / "css"
GUI = ROOT / "mu" / "gui"
LEGACY_ALIASES = frozenset((
    "--accent", "--accent-fg", "--accent-hover", "--accent-line", "--accent-on", "--accent-soft",
    "--accent-solid", "--accent-strong", "--accent-subtle", "--accent-text", "--alpine-green", "--backdrop",
    "--bg", "--bg-active", "--bg-hover", "--bg-lift", "--bg-raised", "--bg-subtle", "--border",
    "--border-focus", "--border-hover", "--border-strong", "--caution", "--caution-subtle", "--code-bg",
    "--composer-bg", "--content-max", "--context-card-highlight", "--context-card-shadow",
    "--context-card-surface", "--context-control-active", "--context-control-active-text",
    "--context-control-surface", "--context-glow-primary", "--context-glow-secondary", "--context-live",
    "--context-live-dot", "--context-plot-surface", "--context-positive", "--context-tooltip-shadow",
    "--context-tooltip-surface", "--context-track-surface", "--control-border", "--control-h",
    "--control-h-sm", "--danger", "--dur-large", "--dur-micro", "--dur-small", "--ease-in", "--ease-out",
    "--ease-standard", "--err", "--error", "--error-subtle", "--err-soft", "--focus-ring", "--font-mono",
    "--font-sans", "--fs-2xl", "--fs-base", "--fs-lg", "--fs-md", "--fs-sm", "--fs-xl", "--fs-xs",
    "--fw-medium", "--fw-regular", "--fw-semibold", "--glacier-blue", "--glass", "--glass-strong",
    "--hairline", "--hairline-color", "--hairline-w", "--header-bg", "--highlight", "--hover", "--info",
    "--info-subtle", "--lh-body", "--lh-snug", "--lh-tight", "--mist-a", "--mist-b", "--mode-accent",
    "--mode-debug", "--mode-feature", "--mode-good", "--mode-loop", "--mode-plan", "--mode-research",
    "--mode-security", "--mode-teacher", "--mode-warn", "--mono", "--mono-font", "--muted", "--ok", "--pane",
    "--plan", "--plan-subtle", "--radius", "--radius-full", "--radius-lg", "--radius-md", "--radius-pill",
    "--radius-sm", "--radius-xl", "--radius-xs", "--raised", "--reading-max", "--risk-high", "--risk-low",
    "--risk-medium", "--running", "--sans", "--scrim", "--serif", "--shadow", "--shadow-1", "--shadow-2",
    "--shadow-card", "--shadow-modal", "--shadow-soft", "--sidebar-bg", "--sky-blue", "--snow-glow",
    "--space-1", "--space-2", "--space-3", "--space-4", "--space-5", "--space-6", "--space-7", "--space-8",
    "--space-9", "--st-active", "--st-done", "--st-failed", "--st-pending", "--st-skipped", "--st-waiting",
    "--success", "--success-subtle", "--sunrise-peach", "--sunrise-pink", "--surface", "--surface-2",
    "--surface-raised", "--surface-strong", "--text", "--text-dim", "--text-dimmer", "--text-on-accent",
    "--text-primary", "--text-secondary", "--text-soft", "--text-tertiary", "--tool", "--touch-target",
    "--tracking-caps", "--tracking-tight", "--viz-1", "--viz-2", "--viz-3", "--viz-4", "--viz-5", "--viz-6",
    "--viz-7", "--viz-8", "--warn", "--warning", "--warning-subtle", "--z-base", "--z-dropdown", "--z-modal",
    "--z-overlay", "--z-popover", "--z-raised", "--z-sticky", "--z-toast",
))


def _sources():
    """Templates, page scripts and every stylesheet (vendored code excluded)."""
    paths = list((GUI / "templates").rglob("*.html")) + list(CSS_ROOT.rglob("*.css"))
    paths += [p for p in (GUI / "static" / "js").rglob("*.js") if "vendor" not in p.parts]
    return sorted(paths)


def test_static_css_holds_only_the_wash_tree():
    assert sorted(p.name for p in CSS_ROOT.iterdir()) == ["wash"]
    for retired in ("app.css", "theme.css", "floating_popouts.css", "tokens.css"):
        assert not (CSS_ROOT / retired).exists(), retired
    assert not (WASH / "patterns" / "legacy-overrides.css").exists()


def test_no_legacy_cascade_layer_or_import_remains():
    for path in sorted(CSS_ROOT.rglob("*.css")):
        text = _strip_comments(path.read_text(encoding="utf-8"))
        assert "layer(legacy)" not in text, path
        assert not re.search(r"@layer[^;{]*\blegacy\b", text), path
        for imported in re.findall(r'@import url\("([^"]+)"\)', text):
            assert not imported.startswith("../"), (path, imported)
    pattern = re.compile(r"/static/css/(?:app|theme|floating_popouts|tokens)\.css")
    offenders = [str(p.relative_to(GUI)) for p in _sources() if pattern.search(p.read_text(encoding="utf-8"))]
    assert not offenders, offenders


def test_no_raw_px_font_sizes_anywhere_in_the_css_tree():
    # Type sizes come from --wash-type-* (spec §4.1); calc() offsets from a
    # token are allowed, a bare `font-size: 13px` is not.
    offenders = []
    for path in sorted(CSS_ROOT.rglob("*.css")):
        if path == TOKENS:
            continue
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(r"font-size\s*:\s*-?\d*\.?\d+px", text):
            offenders.append(f"{path.relative_to(CSS_ROOT)}: {match.group(0)}")
        for match in re.finditer(r"\bfont\s*:([^;{}]*)", text):
            value = re.sub(r"var\([^()]*\)", "", match.group(1))
            value = re.sub(r"calc\([^()]*\)", "", value)
            if re.search(r"(?<![\w.-])\d*\.?\d+px", value):
                offenders.append(f"{path.relative_to(CSS_ROOT)}: font:{match.group(1).strip()}")
    assert not offenders, offenders


def test_no_colour_literals_anywhere_in_the_css_tree():
    offenders = []
    for path in sorted(CSS_ROOT.rglob("*.css")):
        if path == TOKENS:
            continue
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(", text):
            offenders.append(f"{path.relative_to(CSS_ROOT)}: {match.group(0)}")
    assert not offenders, offenders


def test_templates_scripts_and_css_reference_no_legacy_alias_variable():
    # var(--accent), var(--surface), var(--viz-3), muCssToken("--st-done") …
    # must all be the --wash-* equivalent now.
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        names = re.findall(r"var\(\s*(--[\w-]+)", text)
        names += re.findall(r"""(?:muCssToken|getPropertyValue|css|traceToken|traceAlpha|muTokenAlpha)\(\s*['"`](--[\w-]+)""", text)
        for name in names:
            if name in LEGACY_ALIASES:
                offenders.append(f"{path.relative_to(GUI)}: {name}")
    assert not offenders, sorted(set(offenders))


def test_context_charts_map_every_layer_through_its_wash_pigment():
    # Canvas charts read --wash-layer-<id> (L1A and L4 included; the server
    # sends them no hue) and redraw on mucli-theme-change.
    app = (GUI / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'const MU_LAYER_IDS = Object.freeze(["L0", "L1A", "L1B", "L2", "L3", "L4", "L4B", "L5"]);' in app
    assert "return key ? `--wash-layer-${key}` : \"--wash-neutral-pigment\";" in app
    assert 'window.addEventListener("mucli-theme-change", () => this._scheduleRender());' in app
    assert "MU_LAYER_HUE_VIZ" not in app and "_rowHueMap" not in app


def test_one_diff_renderer_and_one_diff_stylesheet():
    # Spec §4.3: one diff module (static/js/wash/diff.js) and one stylesheet
    # (components/diff.css) for chat, approvals, /design and the /work Diff
    # tab; the interim /work renderer is gone.
    js = GUI / "static" / "js"
    assert (js / "wash" / "diff.js").is_file()
    assert not (js / "wash" / "work_diff.js").exists()
    scripts = [p for p in js.rglob("*.js") if "vendor" not in p.parts]
    definers = [p.name for p in scripts if re.search(r"function (renderDiffHTML|renderFile|parseUnified)\b|window\.MuWorkDiff\s*=", p.read_text(encoding="utf-8"))]
    assert definers == ["diff.js"], definers
    api = (js / "wash" / "diff.js").read_text(encoding="utf-8")
    for name in ("renderFile: renderFile", "renderTree: renderTree", "statusBadge: statusBadge", "STATUS_LABEL: STATUS_LABEL"):
        assert name in api, name
    assert "opts.findings" in api and "opts.tree" in api
    for page, needle in (("index.html", '/static/js/wash/diff.js'), ("work.html", '/static/js/wash/diff.js'),
                         ("design/gallery.html", '/static/js/wash/diff.js')):
        assert needle in (GUI / "templates" / page).read_text(encoding="utf-8"), page
    view = (js / "work" / "view.js").read_text(encoding="utf-8")
    assert "const DIFF = window.WashDiff;" in view and "DIFF.renderFile(" in view and "DIFF.renderTree(" in view
    # Diff rows, intra-line marks, findings and the file tree are styled once.
    stylesheets = [p for p in CSS_ROOT.rglob("*.css") if p.name != "diff.css"]
    for path in stylesheets:
        text = _strip_comments(path.read_text(encoding="utf-8"))
        assert not re.search(r"\.(?:w-diff__(?:row|num|sign|code|intra|tree|status|churn|finding)|work-diff-(?:table|intra|finding)|work-st\b|work-cbar)", text), path


def test_one_command_palette_on_every_page():
    # Spec §4.3 / G§9.17, §10.5: static/js/wash/palette.js is the only palette;
    # _layout.html loads it for every page and each page registers its own
    # navigation and actions. /work's local palette (work/ui.js) is gone.
    js = GUI / "static" / "js"
    templates = GUI / "templates"
    scripts = [p for p in js.rglob("*.js") if "vendor" not in p.parts]
    definers = [p.name for p in scripts if "window.WashPalette = {" in p.read_text(encoding="utf-8")]
    assert definers == ["palette.js"], definers
    ui = (js / "work" / "ui.js").read_text(encoding="utf-8")
    assert "work-palette" not in ui and "role=\"listbox\"" not in ui
    assert "window.WashPalette.register('work'" in ui and "function palette() { window.WashPalette?.open(); }" in ui
    css = "".join(p.read_text(encoding="utf-8") for p in CSS_ROOT.rglob("*.css"))
    assert ".work-palette" not in css
    layout = (templates / "_layout.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/wash/palette.js"></script>' in layout
    assert '{% include "partials/palette.html" %}' in layout
    for page in ("index.html", "work.html", "containers.html", "trace.html", "job_trace.html", "model_costs.html"):
        html = (templates / page).read_text(encoding="utf-8")
        assert re.search(r'{% extends "(_layout|base)\.html" %}', html), page
        assert "wash/palette.js" not in html, page  # loaded once, by the layout
    registrations = {
        "/": (js / "wash" / "chat_palette.js", "WashPalette.register('chat:actions'"),
        "/work": (js / "work" / "ui.js", "window.WashPalette.register('work'"),
        "/containers": (js / "containers.js", "window.WashPalette.register('containers'"),
        "/trace": (js / "trace.js", 'window.WashPalette.register("trace"'),
        "/jobs/trace": (js / "job_trace.js", "window.WashPalette.register('job-trace'"),
        "/models/costs": (js / "model_costs.js", "window.WashPalette.register('model-costs'"),
    }
    for page, (path, needle) in registrations.items():
        assert needle in path.read_text(encoding="utf-8"), page
    palette = (js / "wash" / "palette.js").read_text(encoding="utf-8")
    for href in ("'/'", "'/work'", "'/containers'", "'/trace'", "'/jobs/trace'", "'/models/costs'"):
        assert f"href: {href}" in palette, href


# ------------------------------------------------------------ glass restructure (spec §13, G§3.3, §3.4, §6.2)
import sys as _sys

_sys.path.insert(0, str(ROOT / "tests"))
from wash_css_helpers import decls as _decls, rules as _rules  # noqa: E402

SIDE_PROPS = re.compile(r"^border-(?:left|right|inline-start|inline-end)(?:-color)?$")
NEUTRAL_SIDE_VARS = re.compile(r"^--wash-(?:hairline|hairline-strong|glass(?:-[\w-]+)?)$")
SIDE_KEYWORDS = {"transparent", "currentcolor", "none", "0", "solid", "dashed", "dotted", "double",
                 "revert-layer", "inherit", "initial", "unset"}


def _plain(text: str, selector: str) -> dict:
    """Declarations for `selector` from rules outside any @media / @supports."""
    want = re.sub(r"\s+", " ", selector.strip())
    out: dict = {}
    for selectors, body, media in _rules(text):
        if want in selectors and not media:
            out.update(body)
    return out


def _all_rules():
    for path in _wash_files():
        for selectors, body, media in _rules(_strip_comments(path.read_text(encoding="utf-8"))):
            yield path, selectors, body, media


def test_no_coloured_side_borders():
    # G§3.4: surfaces, rows and list items never carry a coloured left/right
    # bar or rule. Side edges are neutral hairlines, glass edges, transparent
    # or currentColor (drawn glyphs such as chevrons and checkmarks).
    offenders = []
    for path, selectors, body, _ in _all_rules():
        for prop, value in body.items():
            if not SIDE_PROPS.match(prop):
                continue
            for name in re.findall(r"var\(\s*(--[\w-]+)", value):
                if not NEUTRAL_SIDE_VARS.match(name):
                    offenders.append(f"{_rel(path)} {selectors[0]}: {prop}: {value}")
            rest = re.sub(r"var\([^()]*(?:\([^()]*\)[^()]*)*\)", " ", value)
            for word in rest.replace(",", " ").split():
                if word.lower() in SIDE_KEYWORDS or re.fullmatch(r"-?[\d.]+(?:px|em|rem)?", word):
                    continue
                offenders.append(f"{_rel(path)} {selectors[0]}: {prop}: {value}")
    assert not offenders, sorted(set(offenders))


def test_no_inset_side_stripes_or_pseudo_element_bars():
    # G§3.4: no `box-shadow: inset Npx 0 …` stripe (N ≥ 2) anywhere, and no
    # thin absolutely-positioned ::before/::after bar painted down a side.
    offenders = []
    for path in _wash_files():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(r"\binset\s+(-?\d+(?:\.\d+)?)px\s+0\b", text):
            if abs(float(match.group(1))) >= 2:
                offenders.append(f"{_rel(path)}: {match.group(0)}")
    for path, selectors, body, _ in _all_rules():
        if not any(re.search(r"::?(?:before|after)\b", s) for s in selectors):
            continue
        width = re.fullmatch(r"(\d+(?:\.\d+)?)px", body.get("width", ""))
        spans = body.get("inset", "").replace(" ", "") in ("0auto00", "000auto") or ("top" in body and "bottom" in body)
        fill = body.get("background", "")
        if width and float(width.group(1)) <= 6 and spans and fill and fill not in ("none", "transparent") \
                and "hairline" not in fill:
            offenders.append(f"{_rel(path)} {selectors[0]}: width {body['width']} background {fill}")
    assert not offenders, offenders


BASE_CSS = WASH / "base.css"
GLASS_RECIPE = {
    "--w-glass-fill": "var(--wash-glass)",
    "--w-glass-fill-strong": "var(--wash-glass-strong)",
    "--w-glass-fill-subtle": "var(--wash-glass-subtle)",
    "--w-glass-line": "var(--wash-glass-border)",
    "--w-glass-ring": "0 0 0 1px var(--wash-glass-edge)",
    "--w-glass-filter": "blur(var(--wash-glass-blur)) saturate(var(--wash-glass-saturate))",
    "--w-glass-filter-strong": "blur(var(--wash-glass-blur-strong)) saturate(var(--wash-glass-saturate))",
    "--w-glass-lift": "var(--wash-glass-highlight), var(--w-glass-ring), var(--wash-glass-shadow)",
}
SOLID = {"--w-glass-fill": "var(--wash-surface)", "--w-glass-fill-strong": "var(--wash-raised)",
         "--w-glass-fill-subtle": "var(--wash-sunken)", "--w-glass-filter": "none", "--w-glass-filter-strong": "none"}
# (file, selector, fill recipe, blurs itself) — G§3.3 levels 1, 1s, 2 and 3.
GLASS_SURFACES = (
    ("layout.css", ".w-topbar", "var(--w-glass-fill)", True),
    ("layout.css", ".w-sidebar", "var(--w-glass-fill)", True),
    ("layout.css", ".w-shell::before", "var(--w-glass-fill)", True),  # the stage plane
    ("components/card.css", ".w-card", "var(--w-glass-fill)", False),
    ("components/approval.css", ".w-approval", "var(--w-glass-fill)", False),
    ("components/composer.css", ".w-composer__form", "var(--w-glass-fill)", False),
    ("components/table.css", ".w-table-wrap", "var(--w-glass-fill)", False),
    ("components/panel.css", ".w-panel", "var(--w-glass-fill)", False),
    ("components/toolcard.css", ".w-toolcard", "var(--w-glass-fill-subtle)", False),
    ("components/menu.css", ".w-menu", "var(--w-glass-fill-strong)", True),
    ("components/menu.css", ".w-popover", "var(--w-glass-fill-strong)", True),
    ("components/toast.css", ".w-toast", "var(--w-glass-fill-strong)", True),
    ("components/palette.css", ".w-palette__dialog", "var(--w-glass-fill-strong)", True),
    ("components/modal.css", ".w-modal__dialog", "var(--w-glass-fill-strong)", True),
    ("components/drawer.css", ".w-drawer__panel", "var(--w-glass-fill-strong)", True),
    ("components/nav.css", '.w-nav__item[aria-current="page"]', "var(--w-glass-fill-strong)", False),
    ("components/session-list.css", '.w-session[aria-current="true"]', "var(--w-glass-fill-strong)", False),
)


def test_glass_recipe_is_built_from_the_glass_tokens():
    base = _strip_comments(BASE_CSS.read_text(encoding="utf-8"))
    recipe = _plain(base, ":root")
    for name, value in GLASS_RECIPE.items():
        assert recipe.get(name) == value, name
    generated = TOKENS.read_text(encoding="utf-8")
    for token in ("--wash-glass", "--wash-glass-strong", "--wash-glass-subtle", "--wash-glass-border",
                  "--wash-glass-edge", "--wash-glass-highlight", "--wash-glass-shadow", "--wash-glass-blur",
                  "--wash-glass-blur-strong", "--wash-glass-saturate"):
        assert f"{token}:" in generated, token


def test_glass_surfaces_use_the_recipe_with_backdrop_filter():
    for name, selector, fill, blurs in GLASS_SURFACES:
        body = _plain(_strip_comments((WASH / name).read_text(encoding="utf-8")), selector)
        assert body.get("background") == fill, (name, selector, body.get("background"))
        if blurs:
            want = "var(--w-glass-filter-strong)" if "strong" in fill else "var(--w-glass-filter)"
            assert body.get("backdrop-filter") == want, (name, selector)
            assert body.get("-webkit-backdrop-filter") == want, (name, selector)
    # Every backdrop-filter in the tree goes through the recipe (so the
    # fallbacks reach it) and always carries its -webkit- twin.
    for path, selectors, body, _ in _all_rules():
        if "backdrop-filter" in body:
            assert body["backdrop-filter"].startswith("var(--w-glass-filter"), (_rel(path), selectors)
            assert body.get("-webkit-backdrop-filter") == body["backdrop-filter"], (_rel(path), selectors)


def test_glass_has_solid_fallbacks_and_a_flat_ambient():
    base = _strip_comments(BASE_CSS.read_text(encoding="utf-8"))
    assert "@supports not (backdrop-filter: blur(1px))" in base
    for media in ("prefers-reduced-transparency: reduce", "forced-colors: active"):
        body = _decls(base, ":root", media=media)
        for name, value in SOLID.items():
            assert body.get(name) == value, (media, name)
        assert _decls(base, "body[data-ambient]::before", media=media)["background"] == "var(--wash-canvas)", media
    supports = base.split("@supports not (backdrop-filter: blur(1px))", 1)[1].split("}\n}", 1)[0]
    for name, value in SOLID.items():
        assert f"{name}: {value};" in supports, name
    reduced = _plain(base, ':root[data-decoration="reduced"]')
    for name, value in SOLID.items():
        assert reduced.get(name) == value, name
    assert _decls(base, ':root[data-decoration="reduced"] body[data-ambient]::before')["background"] == "var(--wash-canvas)"


PAGE_AMBIENTS = {
    "index.html": "dawn", "design/gallery.html": "dawn", "work.html": "lagoon", "trace.html": "lagoon",
    "job_trace.html": "lagoon", "model_costs.html": "lagoon", "containers.html": "meadow",
}


def test_every_layout_page_declares_an_ambient_wash():
    templates = ROOT / "mu" / "gui" / "templates"
    layout = (templates / "_layout.html").read_text(encoding="utf-8")
    assert 'data-ambient="{% block ambient %}dawn{% endblock %}"' in layout
    pages = sorted(str(p.relative_to(templates)) for p in templates.rglob("*.html")
                   if re.search(r'{% extends "(?:_layout|base)\.html" %}', p.read_text(encoding="utf-8"))
                   and p.name != "base.html")
    assert sorted(PAGE_AMBIENTS) == pages
    for page, wash in PAGE_AMBIENTS.items():
        html = (templates / page).read_text(encoding="utf-8")
        assert f"{{% block ambient %}}{wash}{{% endblock %}}" in html, page
    base = _strip_comments(BASE_CSS.read_text(encoding="utf-8"))
    for wash in ("dawn", "lagoon", "meadow"):
        body = _plain(base, f'[data-ambient="{wash}"]')
        assert body["--w-ambient"] == f"var(--wash-art-ambient-{wash})", wash
        assert body["--w-ambient-sm"] == f"var(--w-ambient-{wash}-sm)", wash
        for theme in ("paper", "dusk"):
            assert f'url("/static/wash/art/ambient-{wash}-{theme}-960.webp")' in base, (wash, theme)
            assert (ROOT / "mu" / "gui" / "static" / "wash" / "art" / f"ambient-{wash}-{theme}-960.webp").is_file()
    plane = _plain(base, "body[data-ambient]::before")
    assert plane["position"] == "fixed" and plane["inset"] == "0" and plane["z-index"] == "-1"
    assert "var(--w-ambient, none)" in plane["background"] and "cover" in plane["background"]
    assert _decls(base, "body[data-ambient]::before", media="width < 840px")["background-image"] == "var(--w-ambient-sm, none)"
    # The body and the shell are transparent so the wash shows through the glass.
    assert _plain(base, "body")["background"] == "transparent"
    assert _plain(_strip_comments((WASH / "layout.css").read_text(encoding="utf-8")), ".w-shell")["background"] == "transparent"
