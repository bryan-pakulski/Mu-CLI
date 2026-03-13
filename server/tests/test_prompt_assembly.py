from server.app.runtime.agent_loop import LoopStep
from server.app.runtime.job_runner import (
    DEFAULT_SYSTEM_PROMPT,
    PLANNING_PROMPT_BASE,
    RESEARCH_PROMPT_BASE,
    _build_stage_prompt,
    _build_weighted_context_block,
    _should_enforce_tool_first,
)


def test_weighted_context_block_prioritizes_relevant_messages() -> None:
    messages = [
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "please implement feature and run tests"},
        {"role": "assistant", "content": "error: tests failed with exception"},
    ]
    block = _build_weighted_context_block(messages, 4000)
    assert "error: tests failed" in block
    assert block.splitlines()[0].startswith("- assistant:")


def test_should_enforce_tool_first_for_workspace_actions() -> None:
    step = LoopStep(index=1, label="act", objective="make code changes", success_criteria=["done"])
    assert (
        _should_enforce_tool_first(
            goal="Implement a CLI fix in the repository",
            step=step,
            available_tools=["read_file", "write_file"],
            mode="interactive",
        )
        is True
    )


def test_stage_prompt_is_concise_and_contains_protocol() -> None:
    step = LoopStep(index=0, label="act", objective="Do work", success_criteria=["done"])
    prompt = _build_stage_prompt(
        goal="Implement fix",
        mode="interactive",
        step=step,
        chat_mode=False,
        stage_attempt=1,
        max_stage_turns=3,
        tool_reference_lines=["- read_file: read"],
        skill_reference_lines=["- code-review: review"],
        stage_feedback="",
        citations_required=False,
        context_block="- user: fix bug",
        system_prompt_override=None,
        rules_checklist=None,
    )
    assert "response_protocol" in prompt
    assert "working_memory" in prompt
    assert "available_tools" in prompt


def test_stage_prompt_includes_base_prompts_by_mode() -> None:
    step = LoopStep(index=0, label="explore", objective="research", success_criteria=["cite"])
    prompt = _build_stage_prompt(
        goal="Research topic",
        mode="research",
        step=step,
        chat_mode=False,
        stage_attempt=1,
        max_stage_turns=3,
        tool_reference_lines=[],
        skill_reference_lines=[],
        stage_feedback="",
        citations_required=True,
        context_block="",
        system_prompt_override=None,
        rules_checklist=None,
    )
    assert DEFAULT_SYSTEM_PROMPT in prompt
    assert PLANNING_PROMPT_BASE in prompt
    assert RESEARCH_PROMPT_BASE in prompt
