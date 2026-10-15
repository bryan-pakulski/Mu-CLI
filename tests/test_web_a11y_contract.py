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


# ------------------------------------------------------------ final pass: known gaps (web-final ledger)

def test_dialog_semantics_sit_on_the_surface_not_the_backdrop():
    # /containers modals and the shared file browser: the .w-modal backdrop is
    # the focus-trap root; role="dialog" belongs to its .w-modal__dialog.
    for page in ("containers.html", "fragments/file_browser.html"):
        text = (TEMPLATES / page).read_text(encoding="utf-8")
        for tag, line in _tags(text, r'class="[^"]*\bw-modal\b(?!__)'):
            assert 'role="dialog"' not in tag, (page, line)
        for tag, line in _tags(text, r'class="[^"]*\bw-modal__dialog\b'):
            assert 'role="dialog"' in tag and 'aria-modal="true"' in tag and "aria-labelledby" in tag, (page, line)
    dialogs = (STATIC_JS / "dialogs.js").read_text(encoding="utf-8")
    assert "window.MuDialog = {" in dialogs
    containers = (STATIC_JS / "containers.js").read_text(encoding="utf-8")
    assert "window.MuDialog.open(modal" in containers and "const FOCUSABLE" not in containers


def test_trace_info_popouts_trap_focus():
    text = (TEMPLATES / "trace.html").read_text(encoding="utf-8")
    popouts = list(_tags(text, r'class="trace-info-popout"'))
    assert len(popouts) >= 10
    for tag, line in popouts:
        assert "x-dialog=" in tag and '@dialog-close="infoOpen=null"' in tag, line
        target = re.search(r'aria-labelledby="([^"]+)"', tag).group(1)
        assert f'id="{target}"' in text, line
    assert '/static/js/dialogs.js' in text


def test_destructive_confirms_are_wash_modals_with_cancel_focused():
    confirm = (STATIC_JS / "wash" / "confirm.js").read_text(encoding="utf-8")
    assert 'role="alertdialog" aria-modal="true"' in confirm
    assert "var first = danger ? cancel : ok;" in confirm
    for script, page in (("model_costs.js", "model_costs.html"), ("trace.js", "trace.html")):
        source = (STATIC_JS / script).read_text(encoding="utf-8")
        assert "window.confirm(" not in source, script
        assert "window.WashConfirm({" in source and "tone: " in source, script
        html = (TEMPLATES / page).read_text(encoding="utf-8")
        assert "/static/js/wash/confirm.js" in html and "/static/js/dialogs.js" in html, page


def test_memory_and_history_panel_selects_are_wash_selects():
    for page in ("fragments/memory_panel.html", "fragments/history_panel.html"):
        text = (TEMPLATES / page).read_text(encoding="utf-8")
        selects = re.findall(r"<select\b[^>]*>", text)
        assert selects, page
        for tag in selects:
            assert 'class="w-select__control"' in tag, (page, tag)
        assert text.count('<span class="w-select') == len(selects), page


def test_file_browser_uses_sprite_icons_not_emoji():
    text = (TEMPLATES / "fragments" / "file_browser.html").read_text(encoding="utf-8")
    assert re.search(r"[\U0001F300-\U0001FAFF☀-⛿]", text) is None
    assert "wash.svg#i-' + (e.is_dir ? 'folder' : 'file')" in text


def test_reduce_decoration_preference_is_persisted_and_applied_before_paint():
    bootstrap = (TEMPLATES / "partials" / "theme_bootstrap.html").read_text(encoding="utf-8")
    assert "localStorage.getItem('mucli-decoration')" in bootstrap
    assert "(prefers-reduced-transparency: reduce)" in bootstrap
    assert "root.setAttribute('data-decoration', 'reduced')" in bootstrap
    controller = (STATIC_JS / "route_theme.js").read_text(encoding="utf-8")
    assert "var KEY = 'mucli-decoration';" in controller and "window.MuDecoration" in controller
    drawer = (TEMPLATES / "fragments" / "inspector.html").read_text(encoding="utf-8")
    switch = re.search(r'<input class="w-switch__input"[^>]*data-decoration-toggle[^>]*>', drawer, flags=re.S)
    assert switch and 'role="switch"' in switch.group(0)
    assert ">Reduce decoration</span>" in drawer
    css = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "mu" / "gui" / "static" / "css" / "wash").rglob("*.css"))
    for hook in (':root[data-decoration="reduced"] .w-hero::before', ':root[data-decoration="reduced"] .w-empty__art::before',
                 ':root[data-decoration="reduced"] .w-panel__head::before'):
        assert hook in css, hook


def test_gallery_chat_replay_frame_fits_the_composer():
    gallery = (ROOT / "mu" / "gui" / "static" / "css" / "wash" / "patterns" / "gallery.css").read_text(encoding="utf-8")
    frame = gallery.split(".g-chat__frame {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in frame and "flex-direction: column;" in frame and "overflow: hidden;" in frame
    assert ".g-chat__frame .w-transcript { flex: 1 1 auto; min-height: 0; }" in gallery
    assert ".g-chat__frame .w-composer { flex: none; }" in gallery
