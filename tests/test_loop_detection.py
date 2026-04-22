from core.session import Session
from utils.config import DEFAULT_VARIABLES


def test_tool_sequence_repetition_detection():
    history = [
        "read_file:aaa -> list_dir:bbb",
        "read_file:aaa -> list_dir:bbb",
        "read_file:aaa -> list_dir:bbb",
    ]
    assert Session._is_repeated_tool_sequence(history, repeat_threshold=3) is True


def test_tool_fingerprint_pattern_mode_is_name_only():
    fp = Session._tool_call_fingerprint("read_file", {"filename": "a.py"})
    pattern = Session._tool_call_fingerprint(
        "read_file", {"filename": "different.py"}, pattern_only=True
    )
    assert fp.startswith("read_file:")
    assert pattern == "read_file"


def test_loop_detection_variables_exist():
    assert "loop_detection_enabled" in DEFAULT_VARIABLES
    assert "loop_detection_repeat_threshold" in DEFAULT_VARIABLES
