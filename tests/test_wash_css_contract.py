"""Wash CSS contract (spec §4.1, §8.1), scoped to static/css/wash/.

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
    assert wash.startswith("@layer legacy, tokens, base, layout, components, patterns, utilities;")


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


def test_legacy_stylesheets_carry_no_important():
    # Inside cascade layers `!important` reverses layer priority, so a legacy
    # !important would beat every Wash rule (spec §4.1 migration mechanics).
    legacy = ROOT / "mu" / "gui" / "static" / "css"
    offenders = [
        p.name for p in sorted(legacy.glob("*.css"))
        if "!important" in _strip_comments(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


def test_legacy_token_file_has_no_hex_and_maps_onto_wash():
    tokens = (ROOT / "mu" / "gui" / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", _strip_comments(tokens)) is None
    for legacy, wash in (("--accent", "--wash-accent-ink"), ("--warn", "--wash-attention-ink"),
                         ("--running", "--wash-running-ink"), ("--viz-1", "--wash-layer-L0"),
                         ("--viz-8", "--wash-layer-L5"), ("--surface", "--wash-surface"),
                         ("--bg", "--wash-canvas"), ("--text", "--wash-ink")):
        assert f"{legacy}: var({wash});" in tokens, legacy


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
