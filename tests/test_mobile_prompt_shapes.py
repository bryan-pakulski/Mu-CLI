"""Verify mobile PromptHost renders ALL prompt shapes.

Regression guard for the bug where mobile ``PromptHost.tsx`` filtered
prompts through ``isToolApproval()`` — only ``tool_approval`` shape
rendered, so ``choice`` / ``choices`` / ``quiz`` / ``confirm`` / ``input``
prompts were silently dropped and the agent blocked forever on mobile.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT_HOST = ROOT / "mobile/android/src/components/PromptHost.tsx"
PROMPTS_API = ROOT / "mobile/android/src/api/prompts.ts"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_prompt_host_does_not_filter_by_tool_approval_only():
    """The old bug: mergeQueue/recoverPending filtered isToolApproval(),
    silently dropping every non-approval shape. That helper must be gone."""
    src = read(PROMPT_HOST)
    assert "isToolApproval" not in src, (
        "PromptHost still has isToolApproval() — non-approval shapes "
        "will be silently dropped and the agent will block forever on mobile."
    )


def test_prompt_host_handles_every_known_shape():
    """Every shape the server can emit (see WebUI._ask_prompt) must have
    a dedicated render path in PromptHost. If a shape is missing the agent
    blocks forever on mobile when that shape arrives."""
    src = read(PROMPT_HOST)
    shapes = ["tool_approval", "choice", "choices", "quiz", "confirm", "input"]
    for shape in shapes:
        assert f"'{shape}'" in src or f'"{shape}"' in src, (
            f"PromptHost does not handle shape '{shape}' — agent will block "
            f"on mobile when server sends this prompt shape."
        )


def test_prompt_host_has_per_shape_body_components():
    """Each shape needs its own body component to render the right UI."""
    src = read(PROMPT_HOST)
    for component in [
        "ToolApprovalBody",
        "ChoiceBody",
        "QuizBody",
        "ConfirmBody",
        "InputBody",
    ]:
        assert f"function {component}" in src, (
            f"PromptHost missing body component '{component}'."
        )


def test_prompt_host_does_not_gag_non_approval_shapes_in_queue():
    """Queue merge/recovery must NOT filter by shape — all shapes queue."""
    src = read(PROMPT_HOST)
    # The old code filtered: prompt => isToolApproval(prompt) && ...
    # Ensure no shape filter remains in mergeQueue or recoverPending.
    assert "isToolApproval(prompt)" not in src
    assert "shape === 'tool_approval'" not in src or "case 'tool_approval'" in src


def test_prompts_api_has_quiz_question_type():
    """Quiz prompts carry a questions[] array; the type must exist."""
    src = read(PROMPTS_API)
    assert "QuizQuestion" in src, (
        "prompts.ts missing QuizQuestion interface — quiz shape won't type-check."
    )
    assert "questions?" in src, (
        "PendingPrompt missing questions? field — quiz prompts will be dropped."
    )


def test_choice_body_submits_correct_payloads():
    """Verify the payload formats match what the server (WebUI) expects —
    these mirror app.js promptModal().submit() exactly."""
    src = read(PROMPT_HOST)
    # choice shape → { selected: [...], other_text: "..." }
    assert "selected:" in src and "other_text:" in src
    # choices shape → { value: scalar }
    assert "{ value }" in src or "submit({ value })" in src
    # quiz shape → { answers: {...} }
    assert "submit({ answers })" in src
    # confirm shape → { value: true/false }
    assert "submit({ value: true })" in src
    assert "submit({ value: false })" in src
    # tool_approval → { approved, remember }
    assert "approved: true" in src and "remember" in src
    # input → { value: text }
    assert "submit({ value: text })" in src


def test_prompt_host_has_cancel_for_every_shape():
    """Every prompt must be cancellable — otherwise an unknown/stuck
    prompt blocks the agent forever."""
    src = read(PROMPT_HOST)
    # cancel() helper calls promptsApi.cancel
    assert "promptsApi.cancel" in src
    # CancelButton component exists
    assert "CancelButton" in src