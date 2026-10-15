import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile/android/src"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_safe_area_modal_owns_a_modal_local_provider():
    # Wash: the kit's ModalFrame (src/ui/Sheet.tsx) replaces SafeAreaModal.
    source = read("mobile/android/src/ui/Sheet.tsx")
    assert "SafeAreaProvider" in source
    assert "initialWindowMetrics" in source
    assert "SafeAreaView" in source
    assert "navigationBarTranslucent = false" in source
    assert "edges = ['top', 'bottom']" in source


def test_mobile_feature_code_uses_safe_area_modal_wrapper():
    raw_modal_files = []
    for path in MOBILE.rglob("*.tsx"):
        # The kit's ModalFrame (src/ui/Sheet.tsx) is the one native-modal owner.
        if path.relative_to(MOBILE).as_posix() == "ui/Sheet.tsx":
            continue
        if re.search(r"<Modal[\s>]", path.read_text(encoding="utf-8")):
            raw_modal_files.append(str(path.relative_to(ROOT)))
    assert raw_modal_files == []


def test_full_height_container_and_session_flows_do_not_double_apply_insets():
    for relative in (
        "mobile/android/src/components/ContainerManagerSheet.tsx",
        "mobile/android/src/components/NewSessionSheet.tsx",
        "mobile/android/src/components/WorkspaceSettingsSheet.tsx",
    ):
        source = read(relative)
        assert "ModalFrame" in source or "<Sheet" in source
        assert "useSafeAreaInsets" not in source
        assert "Math.max(insets.bottom" not in source
        assert "navigationBarTranslucent" not in source


def test_drawers_and_bottom_sheets_are_constrained_above_android_navigation():
    # Every sheet and drawer renders through the kit's safe-area ModalFrame.
    assert "<ModalFrame" in read("mobile/android/src/ui/Sheet.tsx")
    for relative in (
        "mobile/android/src/components/SwipeSessionsDrawer.tsx",
        "mobile/android/src/components/ModeDrawer.tsx",
        "mobile/android/src/components/PromptHost.tsx",
    ):
        source = read(relative)
        assert "ModalFrame" in source or "<Sheet" in source, relative
