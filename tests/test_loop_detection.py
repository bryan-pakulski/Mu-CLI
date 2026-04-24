from core.session import Session
from utils.config import DEFAULT_VARIABLES


def test_tool_sequence_repetition_detection():
    history = [
        "read_file:aaa -> list_dir:bbb",
        "read_file:aaa -> list_dir:bbb",
        "read_file:aaa -> list_dir:bbb",
    ]
    assert Session._is_repeated_tool_sequence(history, repeat_threshold=3) is True


def test_tool_fingerprint_pattern_mode_includes_argument_fingerprint():
    fp = Session._tool_call_fingerprint("read_file", {"filename": "a.py"})
    pattern = Session._tool_call_fingerprint(
        "read_file", {"filename": "different.py"}, pattern_only=True
    )
    assert fp.startswith("read_file:")
    assert pattern.startswith("read_file~")


def test_bash_pattern_fingerprint_changes_with_command_args():
    first = Session._tool_call_fingerprint(
        "bash", {"command": "ls -la"}, pattern_only=True
    )
    second = Session._tool_call_fingerprint(
        "bash", {"command": "cat README.md"}, pattern_only=True
    )
    assert first != second


def test_feature_bookkeeping_tools_are_excluded_from_loop_tracking():
    assert Session._track_tool_for_loop_detection("update_task_status", {}) is False
    assert Session._track_tool_for_loop_detection("get_execution_state", {}) is False
    assert Session._track_tool_for_loop_detection("search_for_string", {}) is True


def test_loop_detection_variables_exist():
    assert "loop_detection_enabled" in DEFAULT_VARIABLES
    assert "loop_detection_repeat_threshold" in DEFAULT_VARIABLES
