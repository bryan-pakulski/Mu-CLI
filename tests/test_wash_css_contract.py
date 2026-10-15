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
