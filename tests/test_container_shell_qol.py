# MUCLI_SHELL_QOL_V1
from mu.container.shell_qol import (
    CWD_MARKER_PREFIX,
    CWD_MARKER_SUFFIX,
    CwdMarkerFilter,
    build_completion_response,
    completion_target,
)


def test_completion_target_uses_current_shell_token():
    target = completion_target("python scripts/tra", 18)
    assert target.start == 7
    assert target.prefix == "scripts/tra"


def test_single_completion_adds_space_or_keeps_directory_open():
    file_result = build_completion_response(
        line="cat rea",
        cursor=7,
        candidates=["readme.md"],
        request_id="1",
    )
    assert file_result["replacement"] == "readme.md "

    dir_result = build_completion_response(
        line="cd scr",
        cursor=6,
        candidates=["scripts/"],
    )
    assert dir_result["replacement"] == "scripts/"


def test_multiple_completion_extends_common_prefix():
    result = build_completion_response(
        line="ls requ",
        cursor=7,
        candidates=["requirements-dev.txt", "requirements.txt"],
    )
    assert result["replacement"] == "requirements"


def test_cwd_marker_filter_handles_split_markers():
    parser = CwdMarkerFilter()
    first = parser.feed("hello\n" + CWD_MARKER_PREFIX[:5])
    second = parser.feed(CWD_MARKER_PREFIX[5:] + "/workspace" + CWD_MARKER_SUFFIX + "\nworld\n")
    assert first == "hello\n"
    assert second == "world\n"
    assert parser.cwd == "/workspace"
