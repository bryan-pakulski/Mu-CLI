"""Web accessibility contract (spec §8.1, guide §11)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "mu" / "gui" / "templates"


def _templates():
    return sorted(TEMPLATES.rglob("*.html"))


def _tags(text: str, pattern: str):
    """Yield every opening tag (possibly multi-line) matching `pattern`."""
    for match in re.finditer(r"<[a-zA-Z][^<>]*?>", text, flags=re.S):
        if re.search(pattern, match.group(0)):
            yield match.group(0), text[: match.start()].count("\n") + 1


def test_every_dialog_surface_has_role_modal_and_a_label():
    offenders = []
    for path in _templates():
        text = path.read_text(encoding="utf-8")
        for tag, line in _tags(text, r'role="dialog"'):
            if "aria-labelledby" not in tag and ":aria-labelledby" not in tag:
                offenders.append(f"{path.relative_to(TEMPLATES)}:{line} missing aria-labelledby")
    assert not offenders, offenders


def test_x_dialog_containers_hold_a_labelled_dialog_surface():
    # Every x-dialog (the only modal mechanism, static/js/dialogs.js) wraps or
    # is a role="dialog" element with aria-labelledby.
    offenders = []
    for path in _templates():
        text = path.read_text(encoding="utf-8")
        for tag, line in _tags(text, r'\sx-dialog="'):
            if 'role="dialog"' in tag:
                if "aria-labelledby" not in tag:
                    offenders.append(f"{path.relative_to(TEMPLATES)}:{line}")
                continue
            # Look at the markup that follows the container's opening tag.
            start = text.index(tag)
            window = text[start + len(tag): start + len(tag) + 1200]
            inner = re.search(r"<[a-zA-Z][^<>]*role=\"dialog\"[^<>]*>", window, flags=re.S)
            if not inner or "aria-labelledby" not in inner.group(0):
                offenders.append(f"{path.relative_to(TEMPLATES)}:{line}")
    assert not offenders, offenders


def test_base_modals_live_in_partials_with_dialog_semantics():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    for partial in ("confirm_popover", "loop_add_item_modal", "feature_create_modal",
                    "skills_modal", "tool_cache_modal"):
        assert f'{{% include "partials/{partial}.html" %}}' in base
        text = (TEMPLATES / "partials" / f"{partial}.html").read_text(encoding="utf-8")
        assert 'role="dialog"' in text and 'aria-modal="true"' in text and "aria-labelledby" in text, partial
        for target in re.findall(r'(?<!:)aria-labelledby="([^"]+)"', text):
            assert f'id="{target}"' in text, (partial, target)


def test_trace_page_has_no_click_handlers_on_non_interactive_elements():
    text = (TEMPLATES / "trace.html").read_text(encoding="utf-8")
    offenders = [
        f"line {line}: {tag[:80]}"
        for tag, line in _tags(text, r"^<(div|span|li)\b")
        if re.search(r"(@click(?!\.outside)|x-on:click(?!\.outside)|\sonclick=)", tag)
    ]
    assert not offenders, offenders
    # The former clickable rows are real buttons.
    for cls in ("trace-suspect", "trace-hist-row", "trace-conv-tool", "trace-conv-turn-iters"):
        assert re.search(rf'<button type="button" class="{cls}"', text), cls


def test_toast_regions_are_live_regions():
    region = (TEMPLATES / "partials" / "toast_region.html").read_text(encoding="utf-8")
    assert 'role="status" aria-live="polite"' in region
    assert 'role="alert" aria-live="assertive"' in region
    layout = (TEMPLATES / "_layout.html").read_text(encoding="utf-8")
    assert '{% include "partials/toast_region.html" %}' in layout
    for path in _templates():
        text = path.read_text(encoding="utf-8")
        for tag, line in _tags(text, r'class="[^"]*(toast-container|w-toast-region)'):
            assert "aria-live=" in tag, f"{path.relative_to(TEMPLATES)}:{line}"


def test_icon_only_shell_buttons_are_named():
    macros = (TEMPLATES / "macros" / "wash.html").read_text(encoding="utf-8")
    # btn(variant="icon") always renders aria-label + title from its label.
    assert '{%- if variant == "icon" %} aria-label="{{ label }}" title="{{ label }}"{% endif %}' in macros
    layout = (TEMPLATES / "_layout.html").read_text(encoding="utf-8")
    assert 'aria-controls="w-sidebar" aria-expanded="false" aria-label="Open navigation"' in layout
    assert 'class="w-skip-link" href="#w-main"' in layout
