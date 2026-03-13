from server.app.runtime.agent_loop import _mode_steps


def test_all_modes_have_stage_objectives_and_success_criteria() -> None:
    for mode in ["chat", "interactive", "research", "debugging", "yolo"]:
        steps = _mode_steps(mode)
        assert steps
        for step in steps:
            assert step["label"]
            assert isinstance(step["objective"], str)
            assert step["objective"].strip()
            assert isinstance(step["success_criteria"], list)
            assert step["success_criteria"]
            assert all(isinstance(item, str) and item.strip() for item in step["success_criteria"])


def test_chat_mode_is_single_non_agentic_step() -> None:
    steps = _mode_steps("chat")
    assert len(steps) == 1
    assert steps[0]["label"] == "chat"
