"""Drift-persistent proactive compaction (the "prompt too long AGAIN" fix).

The proactive compaction gates (turn-start roll, auto-hook, preflight) compare
a **cl100k_base** estimate against the provider window divided by a **static**
safety factor. Real-tokenizer drift varies ~2.2–3.2x by content; when it
exceeds the static factor (2.5 for Ollama) the gate passes while the real
prompt already overflows → the 400 fires. The reactive overflow path *measures*
the real drift from the 400 error but used to discard it, so the next turn ran
with the same blind static factor and overflowed again — the "AGAIN".

These tests pin the fix: the measured drift is persisted onto the session
(``_observed_drift_ratio``, EWMA-smoothed), the proactive gates read it via
``effective_drift_ratio`` / ``drift_corrected_context_limit`` and tighten when
learned drift is worse than the static factor, and the cold-cache response
calibration feeds the same store.
"""

from typing import List, Optional

from providers.base import LLMProvider, ProviderResponse
from mu.session.session import Session, SessionManager


# ----------------------------------------------------------- stub providers


class _DriftProvider(LLMProvider):
    """Provider with a >1.0 static safety factor (like Ollama, 2.5) so the
    drift-corrected limit has room to tighten below the static-factor limit."""

    def __init__(self, name: str = "drift-32k", *, window: int = 32_768, factor: float = 2.5):
        super().__init__(name)
        self._window = window
        self.model_name = name
        self._factor = factor

    def get_available_models(self) -> List[str]:
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="ok", parts=[], input_tokens=10, output_tokens=10, total_tokens=20)

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        yield {"type": "text", "text": "ok"}

    def upload_file(self, file_path, mime_type):
        return None

    def effective_context_window(self, model_name: Optional[str] = None):
        return self._window

    def effective_response_reserve(self, model_name: Optional[str] = None):
        return 512

    def compaction_safety_factor(self) -> float:
        return self._factor


def _make_session(provider=None):
    sm = SessionManager()
    session = Session(provider or _DriftProvider(), False, "sys", sm)
    session.agentic = False
    return session


# ----------------------------------------------------------- effective_drift_ratio


def test_effective_drift_ratio_floors_at_static_when_nothing_learned():
    """With no observed drift, the effective ratio is exactly the static
    safety factor — the proactive gates are no less conservative than today."""
    from mu.session.budgets import effective_drift_ratio

    session = _make_session(_DriftProvider(factor=2.5))
    assert not hasattr(session, "_observed_drift_ratio")
    assert effective_drift_ratio(session) == 2.5


def test_effective_drift_ratio_ratchets_up_when_learned_worse():
    """Observed drift above the static factor raises the effective ratio —
    the proactive gates tighten to track the real tokenizer."""
    from mu.session.budgets import effective_drift_ratio, update_observed_drift

    session = _make_session(_DriftProvider(factor=2.5))
    update_observed_drift(session, 3.2)
    assert effective_drift_ratio(session) == 3.2


def test_effective_drift_ratio_corrects_downward_below_static():
    """An observed drift below the static factor (cl100k over-counts for this
    content) lowers the effective ratio — the static factor is a seed, not a
    permanent floor. This is the fix for the permanent 2.5x Ollama inflation
    that made the Memory Map report ~96% full for a ~38%-full session."""
    from mu.session.budgets import effective_drift_ratio, update_observed_drift

    session = _make_session(_DriftProvider(factor=2.5))
    update_observed_drift(session, 1.4)
    assert effective_drift_ratio(session) == 1.4


def test_effective_drift_ratio_floors_at_one_when_cl100k_overcounts():
    """A sub-1.0 observation (real tokens < cl100k estimate) is captured in the
    EWMA but the effective ratio floors at 1.0 — never assume cl100k over-
    counts by more than it does, so the gates never get less conservative
    than 'trust cl100k verbatim'."""
    from mu.session.budgets import effective_drift_ratio, update_observed_drift

    session = _make_session(_DriftProvider(factor=2.5))
    update_observed_drift(session, 0.83)
    assert session._observed_drift_ratio == 0.83
    assert effective_drift_ratio(session) == 1.0


def test_effective_drift_ratio_clamps_pathological_observation():
    """A mis-parsed / pathological drift observation is clamped into the
    [1.0, 6.0] band rather than blowing up the budget."""
    from mu.session.budgets import effective_drift_ratio, update_observed_drift

    session = _make_session(_DriftProvider(factor=2.5))
    update_observed_drift(session, 99.0)
    assert effective_drift_ratio(session) == 6.0


# ----------------------------------------------------------- drift_corrected_context_limit


def test_drift_corrected_limit_unchanged_when_drift_equals_static():
    """When learned drift equals the static factor, the corrected limit equals
    resolve_context_limit (already ÷static). No double-shrink, no grow."""
    from mu.session.budgets import (
        drift_corrected_context_limit,
        resolve_context_limit,
        update_observed_drift,
    )

    session = _make_session(_DriftProvider(window=32_768, factor=2.5))
    update_observed_drift(session, 2.5)  # exactly static
    assert drift_corrected_context_limit(session) == resolve_context_limit(session)


def test_drift_corrected_limit_grows_when_learned_below_static():
    """When observed drift is below the static factor, the corrected limit
    grows back toward the raw window (limit * static / eff). This is the fix
    for the Memory Map fill% staying pinned at ~96%: with a 0.83x real drift
    the limit grows from raw/2.5 to raw/1.0, so fill drops to its true ~38%."""
    from mu.session.budgets import (
        drift_corrected_context_limit,
        resolve_context_limit,
        update_observed_drift,
    )

    session = _make_session(_DriftProvider(window=32_768, factor=2.5))
    static_limit = resolve_context_limit(session)  # 32768 / 2.5 = 13107
    update_observed_drift(session, 1.25)  # half the static factor
    corrected = drift_corrected_context_limit(session)
    # static/eff = 2.5/1.25 = 2.0 -> double the static-factored limit, but
    # capped at the raw window (32768): int(13107 * 2.0) = 26214.
    assert corrected > static_limit
    assert corrected == max(1024, int(static_limit * 2.5 / 1.25))


def test_drift_corrected_limit_capped_at_raw_window_when_cl100k_overcounts():
    """A sub-1.0 effective ratio (floored at 1.0) grows the limit back toward
    the raw window (limit * static / 1.0) — never beyond it."""
    from mu.session.budgets import (
        drift_corrected_context_limit,
        resolve_context_limit,
        update_observed_drift,
    )

    session = _make_session(_DriftProvider(window=32_768, factor=2.5))
    static_limit = resolve_context_limit(session)  # int(32768 / 2.5) = 13107
    update_observed_drift(session, 0.83)  # cl100k over-counts -> floored at 1.0
    corrected = drift_corrected_context_limit(session)
    # int(13107 * 2.5 / 1.0) = int(32767.5) = 32767 — within rounding of raw.
    assert corrected == max(1024, int(static_limit * 2.5 / 1.0))
    assert corrected <= 32_768  # never exceeds the raw window


def test_drift_fill_matches_true_real_fill_after_downward_correction():
    """End-to-end pin of the reported blowup: a 512k window, 2.5x static
    factor, a cl100k estimate of 196k, and a measured real drift of 0.83.
    Before the fix the Memory Map showed 196k / (512k/2.5) = 96%. After the
    fix the effective ratio floors at 1.0, the corrected limit grows to the
    raw 512k, and fill is 196k / 512k = ~38% — the true real-token fill."""
    from mu.session.budgets import (
        drift_corrected_context_limit,
        effective_drift_ratio,
        update_observed_drift,
    )

    session = _make_session(_DriftProvider(window=512_000, factor=2.5))
    cl100k_total = 196_355
    update_observed_drift(session, 0.83)
    assert effective_drift_ratio(session) == 1.0
    corrected_limit = drift_corrected_context_limit(session)
    assert corrected_limit == 512_000
    fill_pct = round(100 * cl100k_total / corrected_limit)
    assert fill_pct == 38, fill_pct  # not 96


def test_drift_corrected_limit_shrinks_when_learned_worse():
    """When observed drift exceeds the static factor, the corrected limit
    shrinks by static/eff so the proactive gates fire earlier."""
    from mu.session.budgets import (
        drift_corrected_context_limit,
        resolve_context_limit,
        update_observed_drift,
    )

    session = _make_session(_DriftProvider(window=32_768, factor=2.5))
    static_limit = resolve_context_limit(session)  # 32768 / 2.5 = 13107
    update_observed_drift(session, 5.0)  # double the static factor
    corrected = drift_corrected_context_limit(session)
    # static/eff = 2.5/5.0 = 0.5 -> half of the static-factored limit.
    assert corrected < static_limit
    assert corrected == max(1024, int(static_limit * 2.5 / 5.0))


def test_drift_corrected_limit_noop_without_static_factor():
    """A provider with safety factor 1.0 (trust cl100k verbatim) never has
    its limit drift-corrected — the static floor path is the only divisor."""
    from mu.session.budgets import (
        drift_corrected_context_limit,
        resolve_context_limit,
        update_observed_drift,
    )

    session = _make_session(_DriftProvider(factor=1.0))
    update_observed_drift(session, 3.0)
    assert drift_corrected_context_limit(session) == resolve_context_limit(session)


# ----------------------------------------------------------- compaction_token_budget


def test_compaction_token_budget_tightens_after_drift_learned():
    """The proactive gate's L5 budget shrinks once a worse-than-static drift
    is observed — the symptom fix: compaction fires before, not after, the
    overflow."""
    from mu.session.budgets import compaction_token_budget, update_observed_drift

    session = _make_session(_DriftProvider(window=32_768, factor=2.5))
    before = compaction_token_budget(session)
    update_observed_drift(session, 5.0)  # worse than the 2.5 static factor
    after = compaction_token_budget(session)
    assert after < before, (before, after)


# ----------------------------------------------------------- update_observed_drift EWMA


def test_update_observed_drift_ewma_merges_second_observation():
    """A second observation is EWMA-blended (weight 0.5) with the prior
    rather than replacing it — a single outlier can't whip the ratio."""
    from mu.session.budgets import update_observed_drift

    session = _make_session()
    update_observed_drift(session, 3.0)
    assert session._observed_drift_ratio == 3.0
    update_observed_drift(session, 5.0)
    # 0.5 * 3.0 + 0.5 * 5.0 = 4.0
    assert session._observed_drift_ratio == 4.0


# ----------------------------------------------------------- reactive path persists drift


def test_aggressive_compact_for_overflow_persists_drift():
    """The reactive overflow compaction must persist the measured drift onto
    the session — the repeat-prevention invariant. After one 400, the next
    turn's proactive gates see the learned drift."""
    from mu.agent.loop_body import _aggressive_compact_for_overflow
    from providers.ollama import OllamaError

    session = _make_session(_DriftProvider(window=100_000, factor=2.5))
    sm = session.session_manager
    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "q"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "a"}]},
    ]
    sm.summary_anchor = 0
    sm.conversation_summary = ""

    # The error body carries ground-truth real counts so _overflow_drift_ratio
    # measures a real ratio (not the static fallback). cl100k of "sys" + the
    # two tiny messages is small, so the parsed 99999 real prompt dominates
    # and the drift clamps to 6.0 — but either way the attribute IS set.
    err = OllamaError(
        "Ollama context overflow for 'drift-32k': "
        '{"error":"The prompt is too long: 99999, '
        'model maximum context length: 100000"}'
    )
    assert not hasattr(session, "_observed_drift_ratio")
    _aggressive_compact_for_overflow(session, "sys", messages=[], overflow_error=err)
    assert hasattr(session, "_observed_drift_ratio")
    assert session._observed_drift_ratio is not None
    assert 1.0 <= session._observed_drift_ratio <= 6.0


def test_aggressive_compact_ewma_merges_second_overflow():
    """A second reactive compaction EWMA-merges with the first persisted
    drift rather than replacing it."""
    from mu.agent.loop_body import _aggressive_compact_for_overflow
    from providers.ollama import OllamaError

    session = _make_session(_DriftProvider(window=100_000, factor=2.5))
    sm = session.session_manager
    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "q"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "a"}]},
    ]
    sm.summary_anchor = 0
    sm.conversation_summary = ""

    err = OllamaError(
        "Ollama context overflow for 'drift-32k': "
        '{"error":"The prompt is too long: 99999, '
        'model maximum context length: 100000"}'
    )
    _aggressive_compact_for_overflow(session, "sys", messages=[], overflow_error=err)
    first = session._observed_drift_ratio

    _aggressive_compact_for_overflow(session, "sys", messages=[], overflow_error=err)
    second = session._observed_drift_ratio
    # EWMA: 0.5*first + 0.5*first == first (same observation) -> unchanged.
    assert second == first


# ----------------------------------------------------------- cold-cache calibration


class _FakeResponse:
    def __init__(self, input_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = 0
        self.total_tokens = input_tokens


def test_cold_cache_calibration_updates_drift_on_strong_signal():
    """A cold-cache response whose input_tokens is a strong full-prompt
    signal (>= half the stashed cl100k estimate, > 500) calibrates drift."""
    from mu.agent.loop_body import _calibrate_drift_from_response

    session = _make_session(_DriftProvider(factor=2.5))
    # Preflight stashed a 10k cl100k estimate for this prompt.
    session._last_prompt_cl100k_est = 10_000
    # Cold cache: provider reports ~26k real tokens for that 10k cl100k prompt
    # -> drift ~2.6, a strong full-prompt signal (>= 5000 and > 500).
    _calibrate_drift_from_response(session, _FakeResponse(input_tokens=26_000))
    assert hasattr(session, "_observed_drift_ratio")
    assert session._observed_drift_ratio == 26_000 / 10_000


def test_cold_cache_calibration_ignores_warm_cache_near_zero_delta():
    """A warm-cache near-zero prompt_eval_count delta must NOT calibrate —
    it would record a bogus sub-1.0 drift and (after clamping) corrupt the
    learned ratio."""
    from mu.agent.loop_body import _calibrate_drift_from_response

    session = _make_session(_DriftProvider(factor=2.5))
    session._last_prompt_cl100k_est = 10_000
    # Warm cache: only 12 non-cached tokens reported for a 10k-cl100k prompt.
    _calibrate_drift_from_response(session, _FakeResponse(input_tokens=12))
    assert not hasattr(session, "_observed_drift_ratio"), "warm delta must not calibrate"


def test_cold_cache_calibration_ignores_weak_cl100k_estimate():
    """When the stashed cl100k estimate itself is tiny (< 1000), the ratio
    would be noise — calibration is skipped."""
    from mu.agent.loop_body import _calibrate_drift_from_response

    session = _make_session(_DriftProvider(factor=2.5))
    session._last_prompt_cl100k_est = 400
    _calibrate_drift_from_response(session, _FakeResponse(input_tokens=300))
    assert not hasattr(session, "_observed_drift_ratio")


def test_cold_cache_calibration_ignores_below_half_signal():
    """A reported count below half the cl100k estimate is ambiguous (partial
    cache hit), not a clean full-prompt signal — calibration is skipped."""
    from mu.agent.loop_body import _calibrate_drift_from_response

    session = _make_session(_DriftProvider(factor=2.5))
    session._last_prompt_cl100k_est = 10_000
    # 4000 < 5000 (half) even though > 500 -> skipped.
    _calibrate_drift_from_response(session, _FakeResponse(input_tokens=4_000))
    assert not hasattr(session, "_observed_drift_ratio")