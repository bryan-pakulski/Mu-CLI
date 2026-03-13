from server.app.runtime.job_runner import (
    _is_user_facing_context_message,
    _looks_like_internal_prompt_echo,
)


def test_detects_internal_prompt_echo() -> None:
    content = """goal=Write app
mode=interactive
step=plan

available_tools_by_name_and_usage:
- read_file

stage_protocol:
- use prefix
"""
    assert _looks_like_internal_prompt_echo(content) is True


def test_accepts_normal_assistant_message() -> None:
    content = "Implemented hello_world.py and verified it prints Hello World."
    assert _looks_like_internal_prompt_echo(content) is False


def test_filters_non_user_facing_context_messages() -> None:
    internal = {
        "role": "assistant",
        "content": "available_tools_by_name_and_usage:\n- read_file",
    }
    normal = {"role": "assistant", "content": "Created file and ran test."}
    assert _is_user_facing_context_message(internal) is False
    assert _is_user_facing_context_message(normal) is True
