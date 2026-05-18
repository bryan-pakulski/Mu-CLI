"""Pin the /set layer + /get layer ergonomic shortcuts."""

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

import mu.commands as mc
from mu.session.session import Session, SessionManager
from mu.commands.variables import LAYER_BUDGET_VARS
from providers.base import LLMProvider, ProviderResponse
from mu.ui.input import InputHandler


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def upload_file(self, file_path, mime_type):
        return None


@pytest.fixture
def session():
    sm = SessionManager()
    s = Session(_DummyProvider("dummy"), False, "system instruction", sm)
    s._mcp_clients = []
    s.session_manager.history = []
    s.session_manager.summary_anchor = 0
    s.session_manager.provider_config = {"provider": "openai", "model": "gpt-4o"}
    return s


# ----------------------------------------------- mapping coverage


def test_layer_budget_vars_cover_l1_through_l4b():
    """Every layer with a per-layer budget (L1, L1B, L2, L3, L4, L4B)
    has an entry. L5 is intentionally absent."""
    assert set(LAYER_BUDGET_VARS.keys()) == {"L1", "L1B", "L2", "L3", "L4", "L4B"}
    for layer_id, (var_name, _label, _desc) in LAYER_BUDGET_VARS.items():
        assert var_name, f"{layer_id} has empty variable name"


def test_layer_budget_vars_match_schema():
    """Each layer maps to a variable that actually exists in
    VARIABLE_SCHEMA, so /set + /unset behave correctly."""
    from utils.config import VARIABLE_SCHEMA

    for layer_id, (var_name, _label, _desc) in LAYER_BUDGET_VARS.items():
        assert var_name in VARIABLE_SCHEMA, (
            f"/set layer {layer_id} → {var_name} not in VARIABLE_SCHEMA"
        )


# ----------------------------------------------- /set layer


def test_set_layer_value_is_tokens_not_chars(session):
    """The value passed to /set layer is TOKENS (matching the unit
    shown in /memory). Internally it's stored as chars at the 4:1
    ratio — so `/set layer L4 6000` should land 24000 in the
    `_chars` variable. This is the regression-pin for the bug where
    bumping L1 made it smaller because the user typed tokens but the
    command interpreted chars."""
    result = mc.dispatch(session, "/set layer L4 6000", allow_prompt=False)
    assert result.ok
    assert session.variables["recent_tool_context_char_limit"] == 24000
    assert result.data["layer"] == "L4"
    assert result.data["variable"] == "recent_tool_context_char_limit"
    assert result.data["tokens"] == 6000
    assert result.data["chars"] == 24000


def test_set_layer_is_case_insensitive(session):
    """`/set layer l1b 1024` should work just as well as `L1B`."""
    result = mc.dispatch(session, "/set layer l1b 1024", allow_prompt=False)
    assert result.ok
    # 1024 tokens → 4096 chars
    assert session.variables["skills_max_chars"] == 4096


@pytest.mark.parametrize(
    "layer,expected_var",
    [
        ("L1", "workspace_context_max_chars"),
        ("L1B", "skills_max_chars"),
        ("L2", "conversation_summary_char_limit"),
        ("L3", "active_goal_context_char_limit"),
        ("L4", "recent_tool_context_char_limit"),
        ("L4B", "retrieval_context_char_limit"),
    ],
)
def test_set_layer_routes_each_id_to_correct_variable(session, layer, expected_var):
    result = mc.dispatch(session, f"/set layer {layer} 1000", allow_prompt=False)
    assert result.ok
    # 1000 tokens → 4000 chars
    assert session.variables[expected_var] == 4000


def test_set_layer_rejects_l5(session):
    """L5 has no per-layer budget — error must point at context_token_limit."""
    result = mc.dispatch(session, "/set layer L5 50000", allow_prompt=False)
    assert not result.ok
    assert "context_token_limit" in result.message


def test_set_layer_rejects_l0(session):
    """L0 is the system prompt — not char-budgeted."""
    result = mc.dispatch(session, "/set layer L0 5000", allow_prompt=False)
    assert not result.ok
    assert "system" in result.message.lower()
    assert "--system" in result.message or "system_instruction" in result.message


def test_get_layer_l0_explains_no_budget(session):
    result = mc.dispatch(session, "/get layer L0", allow_prompt=False)
    assert result.ok
    assert "no char budget" in result.message
    assert "/memory list L0" in result.message


def test_set_layer_rejects_unknown_id(session):
    result = mc.dispatch(session, "/set layer L99 12345", allow_prompt=False)
    assert not result.ok
    assert "Unknown layer" in result.message


def test_set_layer_requires_two_args(session):
    result = mc.dispatch(session, "/set layer L1", allow_prompt=False)
    assert not result.ok
    assert "Usage" in result.message


def test_set_layer_rejects_non_integer(session):
    result = mc.dispatch(session, "/set layer L1 not-a-number", allow_prompt=False)
    assert not result.ok


def test_set_layer_rejects_zero_or_negative(session):
    """A zero-token budget would disable the layer entirely — make
    users state that explicitly via the underlying variable, not as a
    typo on the shortcut."""
    assert not mc.dispatch(session, "/set layer L1 0", allow_prompt=False).ok
    assert not mc.dispatch(session, "/set layer L1 -100", allow_prompt=False).ok


def test_set_layer_round_trip_in_tokens(session):
    """The user-facing flow: type a token count, see the same number
    back via /get layer + the /memory table denominator."""
    mc.dispatch(session, "/set layer L1 4096", allow_prompt=False)
    result = mc.dispatch(session, "/get layer L1", allow_prompt=False)
    assert result.data["tokens"] == 4096
    # And the /memory table's "maximum" for L1 should match.
    from utils.runtime_metrics import collect_context_layers

    layers = collect_context_layers(session)
    l1 = next(layer for layer in layers if layer["layer"] == "L1")
    assert l1["maximum"] == 4096, (
        f"L1 maximum in /memory is {l1['maximum']} tokens but user set 4096"
    )


# ----------------------------------------------- /get layer


def test_get_layer_with_id_returns_current_value(session):
    """1000 tokens stored, /get should report tokens AND chars."""
    session.variables["recent_tool_context_char_limit"] = 4000
    result = mc.dispatch(session, "/get layer L4", allow_prompt=False)
    assert result.ok
    assert result.data["layer"] == "L4"
    assert result.data["variable"] == "recent_tool_context_char_limit"
    assert result.data["tokens"] == 1000
    assert result.data["chars"] == 4000


def test_get_layer_no_id_lists_all_budgets(session):
    result = mc.dispatch(session, "/get layer", allow_prompt=False)
    assert result.ok
    budgets = result.data["layer_budgets"]
    assert len(budgets) == 6
    layer_ids = {row["layer"] for row in budgets}
    assert layer_ids == {"L1", "L1B", "L2", "L3", "L4", "L4B"}


def test_get_layer_l5_explains_no_budget(session):
    result = mc.dispatch(session, "/get layer L5", allow_prompt=False)
    assert result.ok
    assert "no per-layer budget" in result.message


def test_get_layer_unknown_id_errors(session):
    result = mc.dispatch(session, "/get layer L99", allow_prompt=False)
    assert not result.ok


# ----------------------------------------------- normal /set / /get still work


def test_set_still_handles_regular_variables(session):
    """The layer shortcut must not break the generic /set path."""
    result = mc.dispatch(session, "/set yolo true", allow_prompt=False)
    assert result.ok
    assert session.variables["yolo"] is True


def test_set_layer_persists_via_get(session):
    """Round-trip: /set layer L2 4000 then /get layer L2 returns 4000 tokens."""
    mc.dispatch(session, "/set layer L2 4000", allow_prompt=False)
    result = mc.dispatch(session, "/get layer L2", allow_prompt=False)
    assert result.data["tokens"] == 4000


def test_unset_works_via_underlying_variable_name(session):
    """`/unset` operates on the underlying variable name, not the layer ID.
    This is intentional — `/unset layer L4` would be a separate UX
    decision; for now, restore the default via the variable name."""
    session.variables["recent_tool_context_char_limit"] = 24000
    result = mc.dispatch(
        session, "/unset recent_tool_context_char_limit", allow_prompt=False
    )
    assert result.ok
    assert session.variables["recent_tool_context_char_limit"] == 12000  # schema default


# ----------------------------------------------- autocomplete


def _completion_texts(handler: InputHandler, text: str):
    doc = Document(text=text, cursor_position=len(text))
    completions = list(
        handler.completer.get_completions(
            doc, CompleteEvent(completion_requested=True)
        )
    )
    return {c.text for c in completions}


def test_autocomplete_set_layer_suggests_keyword():
    """`/set la<Tab>` should suggest the `layer` keyword."""
    handler = InputHandler()
    completions = _completion_texts(handler, "/set la")
    assert "layer" in completions


def test_autocomplete_set_layer_id_suggestions():
    """`/set layer <Tab>` should offer the 6 layer IDs (no L5)."""
    handler = InputHandler()
    completions = _completion_texts(handler, "/set layer ")
    for layer_id in ("L1", "L1B", "L2", "L3", "L4", "L4B"):
        assert layer_id in completions, f"missing {layer_id}"
    assert "L5" not in completions


def test_autocomplete_set_layer_prefix_filter():
    """`/set layer L1<Tab>` should narrow to L1 and L1B."""
    handler = InputHandler()
    completions = _completion_texts(handler, "/set layer L1")
    assert "L1" in completions
    assert "L1B" in completions
    assert "L2" not in completions


def test_autocomplete_get_layer_id_suggestions():
    """`/get layer <Tab>` should suggest layer IDs too."""
    handler = InputHandler()
    completions = _completion_texts(handler, "/get layer ")
    for layer_id in ("L1", "L1B", "L2", "L3", "L4", "L4B"):
        assert layer_id in completions, f"missing {layer_id}"
