from server.app.runtime.job_runner import _extract_stage_signal


def test_extract_stage_signal_ready_with_body() -> None:
    output = """STAGE_READY::plan::

## Plan: Python Hello World App
- create hello_world.py
"""
    is_ready, signal, cleaned = _extract_stage_signal(output, "plan")
    assert is_ready is True
    assert signal == "ready"
    assert "## Plan" in cleaned


def test_extract_stage_signal_ready_with_leading_whitespace() -> None:
    output = "\n  STAGE_READY::plan::\nDone"
    is_ready, signal, cleaned = _extract_stage_signal(output, "plan")
    assert is_ready is True
    assert signal == "ready"
    assert cleaned == "Done"


def test_extract_stage_signal_needs_more() -> None:
    output = "STAGE_NEEDS_MORE::plan::Need file path"
    is_ready, signal, cleaned = _extract_stage_signal(output, "plan")
    assert is_ready is False
    assert signal == "needs_more"
    assert cleaned == "Need file path"


def test_extract_stage_signal_wrong_stage_name_not_ready() -> None:
    output = "STAGE_READY::act::Did act"
    is_ready, signal, cleaned = _extract_stage_signal(output, "plan")
    assert is_ready is False
    assert signal == "ready"
    assert cleaned == "Did act"
