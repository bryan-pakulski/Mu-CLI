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


# ------------------------------------------------------------ W4 pages
W4_PAGES = ("work.html", "containers.html", "trace.html", "job_trace.html", "model_costs.html")
STATIC_JS = ROOT / "mu" / "gui" / "static" / "js"


def test_w4_pages_have_no_click_handlers_on_non_interactive_elements():
    for page in W4_PAGES:
        text = (TEMPLATES / page).read_text(encoding="utf-8")
        offenders = [
            f"{page}:{line}: {tag[:80]}"
            for tag, line in _tags(text, r"^<(div|span|li|section|article)\b")
            if re.search(r"(@click(?!\.outside)|x-on:click(?!\.outside)|\sonclick=)", tag)
        ]
        assert not offenders, offenders


def test_w4_dialogs_label_themselves_with_an_existing_title():
    for page in ("work.html", "containers.html"):
        text = (TEMPLATES / page).read_text(encoding="utf-8")
        for tag, line in _tags(text, r'role="dialog"'):
            assert 'aria-modal="true"' in tag, (page, line)
            target = re.search(r'aria-labelledby="([^"]+)"', tag).group(1)
            assert f'id="{target}"' in text, (page, line, target)
    # Script-built /work dialogs (work/ui.js) use the same labelled modal surface.
    ui = (STATIC_JS / "work" / "ui.js").read_text(encoding="utf-8")
    assert 'class="w-modal__dialog work-dialog' in ui
    assert 'role="dialog" aria-modal="true" aria-labelledby="${titleId}"' in ui
    assert '<h2 class="w-modal__title" id="${titleId}">' in ui
    # Destructive confirms give Cancel the default focus (G§9.17).
    assert "foot.querySelector(danger ? '[data-cancel]' : '[data-ok]').focus();" in ui


def test_work_toasts_use_the_shared_live_regions():
    # One toast system (G§9.16): /work pushes through window.WashToast into the
    # layout's polite / assertive regions instead of its own stack.
    html = (TEMPLATES / "work.html").read_text(encoding="utf-8")
    ui = (STATIC_JS / "work" / "ui.js").read_text(encoding="utf-8")
    assert "data-toast-stack" not in html and 'id="work-toast"' not in html
    assert "window.WashToast?.show(" in ui


def test_work_job_page_controls_expose_their_state():
    view = (STATIC_JS / "work" / "view.js").read_text(encoding="utf-8")
    # Tabs: tablist with selected state and roving tabindex; arrow keys move.
    assert 'role="tablist" aria-label="Job views"' in view and 'role="tab"' in view and 'aria-controls="work-panel-${t}"' in view
    assert "e.key === 'ArrowRight'" in view and "e.key === 'ArrowLeft'" in view
    # Overflow menu: a menu button with expanded state, Esc closes and returns focus.
    assert 'aria-haspopup="menu" aria-expanded="${wasOpen}" aria-controls="work-actions-menu"' in view
    assert 'role="menu" id="work-actions-menu"' in view and 'role="menuitem"' in view
    assert "if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }" in view
    # Pipeline steps are buttons (they open the step's tab), with the current step marked.
    assert '<button type="button" class="work-step" data-step=' in view and "aria-current=\"step\"" in view
    # Status is glyph + label, never colour alone.
    assert '<span class="w-sr-only">${esc(text)}</span>' in view


def test_chat_signature_components_are_named_and_announced():
    chat = (TEMPLATES / "fragments" / "chat.html").read_text(encoding="utf-8")
    # The approval card is a labelled group and new prompts are announced
    # assertively (G§9.9, §11).
    card = chat[chat.index('<section class="msg prompt w-approval"'):]
    card = card[:card.index('<header class="w-approval__head">')]
    assert 'role="group"' in card and ':aria-labelledby="' in card
    assert 'role="alert" aria-live="assertive"' in chat
    # Every icon-only button in the chat column carries a name.
    for tag, line in _tags(chat, r'data-variant="icon"'):
        if tag.startswith("<button"):
            assert "aria-label" in tag, f"fragments/chat.html:{line}"
    # Sessions and threads rows are keyboard operable (G§9.20).
    index = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    for tag, line in _tags(index, r'class="nav-item w-session"'):
        assert 'role="button"' in tag and 'tabindex="0"' in tag, f"index.html:{line}"
    # The command palette dialog is labelled.
    palette = (TEMPLATES.parent / "static" / "js" / "wash" / "palette.js").read_text(encoding="utf-8")
    assert 'role="dialog" aria-modal="true" aria-labelledby="' in palette
