from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_empty_history_is_hydrated_once_instead_of_every_poll():
    source = read("mobile/android/src/hooks/useChatSession.ts")
    assert "historyHydratedRef" in source
    assert "historyHydratedRef.current !== activeSessionName" in source
    assert "wasBusy || messagesRef.current.length === 0" not in source
    assert "historyRequestRef" in source
    assert "inFlight?.sessionName === activeSessionName" in source


def test_history_refresh_does_not_replace_identical_messages():
    source = read("mobile/android/src/hooks/useChatSession.ts")
    assert "const unchanged = current.length === historyMessages.length" in source
    assert "return unchanged ? current : historyMessages" in source
    assert "lastSessionRef.current !== activeSessionName" in source


def test_chat_keeps_one_composer_mounted_across_empty_and_loading_states():
    source = read("mobile/android/src/screens/ChatScreen.tsx")
    assert "if (messages.length === 0 && !waitingForFirstToken && !historyLoading)" not in source
    assert "messages.length === 0 ? styles.messageListEmpty : null" in source
    assert "ListEmptyComponent" in source
    assert source.count("<Composer") == 1


def test_keyboard_and_draft_remain_available_during_background_updates():
    source = read("mobile/android/src/screens/ChatScreen.tsx")
    assert 'keyboardShouldPersistTaps="always"' in source
    assert 'keyboardDismissMode="none"' in source
    assert "editable={!streaming}" not in source
    assert "Keep focus and the draft keyboard open while a turn is running" in source


def test_chat_subscribes_only_to_connection_fields_it_renders():
    source = read("mobile/android/src/screens/ChatScreen.tsx")
    assert "const connection = useConnectionStore();" not in source
    assert "useConnectionStore(state => state.activeSessionName)" in source
    assert "useConnectionStore(state => state.activeProvider)" in source
