from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_web_session_rows_are_primary_action_with_compact_delete():
    source = read("mu/gui/templates/fragments/welcome.html")
    assert 'class="welcome-session-row" role="button" tabindex="0"' in source
    assert '@click="loadSession(s.name)"' in source
    assert '@keydown.enter.prevent="loadSession(s.name)"' in source
    assert '@click.stop="$store.confirm.ask(`Delete session' in source
    assert 'class="welcome-row-icon danger-text"' in source
    assert '>load</button>' not in source
    assert '>delete</button>' not in source


def test_web_container_cards_use_direct_edit_and_overflow_actions():
    source = read("mu/gui/static/js/containers.js")
    assert 'class="manager-card manager-card-interactive" data-card-action="edit"' in source
    assert 'class="manager-overflow"' in source
    assert 'class="manager-overflow-menu"' in source
    assert 'Open configuration' in source
    assert 'manager-actions manager-actions-primary' not in source
    assert 'manager-actions manager-actions-secondary' not in source
    assert 'article class="manager-card manager-template-card manager-card-interactive"' in source
    assert 'data-template-action="remove"' in source


def test_mobile_session_lists_open_on_row_press_and_use_delete_icon():
    source = read("mobile/android/src/screens/SessionsScreen.tsx")
    assert 'onPress={() => switchSession(item.name)}' in source
    assert 'accessibilityLabel={`Delete session ${item.name}`}' in source
    assert '<Button title="Switch"' not in source
    assert '<Button title="Delete"' not in source
    assert '<Button title="New Session"' not in source


def test_mobile_container_cards_use_action_sheet_instead_of_button_grid():
    source = read("mobile/android/src/components/ContainerManagerSheet.tsx")
    assert 'onPress={() => openEdit(container, false)}' in source
    assert 'setActionContainer(container)' in source
    assert 'visible={actionContainer !== null}' in source
    assert 'function ActionRow(' in source
    assert 'function Action(' not in source
    assert 'styles.actionGrid' not in source
    assert 'label="Remove"' not in source
