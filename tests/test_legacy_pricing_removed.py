"""The Gemini-only legacy pricing map is gone; every cost estimate goes
through the versioned pricing registry in utils/model_pricing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils.model_pricing import estimate_session_cost, resolve_token_pricing, session_cost_summary


def _session(provider_name, model, variables=None, host=""):
    provider = SimpleNamespace(name=provider_name, model_name=model)
    if host:
        provider.host = host
    return SimpleNamespace(provider=provider, variables=variables or {})


def test_legacy_pricing_symbols_are_removed():
    import utils.config as config

    assert not hasattr(config, "PRICING_DB")
    assert not hasattr(config, "calculate_cost")
    assert not hasattr(config, "KNOWN_MODELS")


def test_no_module_imports_the_legacy_calculator():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in list((root / "mu").rglob("*.py")) + list((root / "utils").rglob("*.py")) + [root / "mucli.py"]:
        text = path.read_text(encoding="utf-8")
        if "calculate_cost(" in text or "PRICING_DB" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_session_cost_uses_registry_rates_for_every_provider():
    anthropic = estimate_session_cost(
        _session("anthropic", "claude-opus-5"),
        input_tokens=1_000_000, output_tokens=100_000, cached_tokens=500_000,
    )
    # 500k uncached @ $5 + 500k cached @ $0.50 + 100k out @ $25
    assert anthropic == pytest.approx(2.5 + 0.25 + 2.5)

    gemini = estimate_session_cost(
        _session("gemini", "models/gemini-3.6-flash"),
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert gemini == pytest.approx(1.5 + 7.5)

    openai = estimate_session_cost(
        _session("openai", "gpt-5.4-mini"), input_tokens=1_000_000, output_tokens=0
    )
    assert openai == pytest.approx(0.75)


def test_session_cost_distinguishes_local_and_cloud_ollama():
    local = estimate_session_cost(
        _session("ollama", "glm-5.2", {"ollama_mode": "local"}, host="http://127.0.0.1:11434"),
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert local == 0.0

    cloud = estimate_session_cost(
        _session("ollama", "glm-5.2:cloud", {"ollama_mode": "cloud"}, host="https://ollama.com"),
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert cloud == pytest.approx(1.4 + 4.4)


def test_unknown_model_is_unpriced_not_zero_and_missing_provider_is_safe():
    assert estimate_session_cost(_session("openai", "mystery-model"), input_tokens=10) is None
    assert estimate_session_cost(SimpleNamespace(provider=None, variables={})) is None


def _session_with_counts(provider_name, model, counts, variables=None, host="https://ollama.com"):
    session = _session(provider_name, model, variables, host=host)
    session.session_manager = SimpleNamespace(token_counts=dict(counts))
    return session


def test_ollama_cloud_catalog_matches_published_rates():
    # ollama.com/pricing, fetched 2026-09-24
    expected = {
        "glm-5.3": (1.40, 0.26, 4.40),
        "glm-5.3-flash": (0.15, 0.03, 0.50),
        "glm-5.2": (1.40, 0.26, 4.40),
        "kimi-k2.7-code": (0.95, 0.19, 4.00),
        "gpt-oss:20b": (0.07, 0.035, 0.30),
        "gpt-oss:120b": (0.15, 0.014, 0.60),
    }
    for model, (inp, cached, out) in expected.items():
        row = resolve_token_pricing("ollama", model)
        assert row is not None, model
        assert row.input_per_million == pytest.approx(inp), model
        assert row.cached_input_per_million == pytest.approx(cached), model
        assert row.output_per_million == pytest.approx(out), model
    # glm-5.3 must not fall through to the flash row (or vice versa)
    assert resolve_token_pricing("ollama", "glm-5.3").key == "glm-5.3:cloud"
    assert resolve_token_pricing("ollama", "glm-5.3-flash:cloud").key == "glm-5.3-flash:cloud"


def test_cloud_priced_models_are_still_free_when_served_locally():
    local = estimate_session_cost(
        _session("ollama", "gpt-oss:20b", {"ollama_mode": "local"}, host="http://localhost:11434"),
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert local == 0.0
    cloud = estimate_session_cost(
        _session("ollama", "gpt-oss:20b", {"ollama_mode": "cloud"}),
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert cloud == pytest.approx(0.07 + 0.30)


def test_session_cost_summary_prefers_accumulated_per_request_total():
    # A tiered OpenAI model: per-request accumulation is authoritative and
    # must NOT be replaced by a bulk recomputation over session totals.
    session = _session_with_counts(
        "openai", "gpt-5.6-sol",
        {"input": 2_000_000, "output": 10_000, "cached": 0, "total_cost": 10.30},
    )
    summary = session_cost_summary(session)
    assert summary["source"] == "accumulated"
    assert summary["cost_usd"] == pytest.approx(10.30)


def test_session_cost_summary_recomputes_legacy_zero_totals():
    # Legacy Ollama Cloud session priced under the Gemini-only map: stored
    # total is 0 while tokens are not. No tiers -> bulk equals per-request.
    session = _session_with_counts(
        "ollama", "glm-5.3",
        {"input": 190_975_257, "output": 377_488, "cached": 0, "total_cost": 0.0},
        {"ollama_mode": "cloud"},
    )
    summary = session_cost_summary(session)
    assert summary["source"] == "recomputed_from_totals"
    assert summary["cost_usd"] == pytest.approx(190.975257 * 1.40 + 0.377488 * 4.40)

    local = _session_with_counts(
        "ollama", "glm-5.3", {"input": 1000, "output": 10, "total_cost": 0.0},
        {"ollama_mode": "local"}, host="http://localhost:11434",
    )
    assert session_cost_summary(local) == {
        "cost_usd": 0.0, "source": "accumulated", "accumulated_cost_usd": 0.0,
    }


def test_gui_stats_and_cli_metrics_report_the_same_cost():
    import asyncio
    from mu.gui.routers.inspector import get_stats
    from utils.runtime_metrics import _session_cost_summary

    session = _session_with_counts(
        "ollama", "glm-5.3-flash",
        {"input": 512_363_448, "output": 109_864, "cached": 0, "total": 0, "total_cost": 0.0},
        {"ollama_mode": "cloud", "agent_mode": "default"},
    )
    expected = 512.363448 * 0.15 + 0.109864 * 0.50
    assert _session_cost_summary(session)["cost_usd"] == pytest.approx(expected)

    sm = session.session_manager
    sm.current_session_name = "mucli"; sm.history = []
    sm.task_memory = SimpleNamespace(entries=[], status_counts=lambda: {})
    sm.turn_scratchpad = SimpleNamespace(entries=[])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_by_name=lambda name=None: session)))
    stats = asyncio.run(get_stats(request))
    assert stats["estimated_cost_usd"] == pytest.approx(expected)
    assert stats["cost_source"] == "recomputed_from_totals"
    assert stats["tokens"]["total_cost"] == pytest.approx(expected)


def test_operator_override_does_not_hide_newer_packaged_rows(tmp_path, monkeypatch):
    from utils.model_pricing import pricing_catalog, save_pricing_config

    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "override-home"))
    packaged = pricing_catalog()
    assert packaged["using_override"] is False
    # An old override: only the OpenAI rows plus a custom glm-5.2 rate.
    subset = [dict(m) for m in packaged["models"] if m["provider"] == "openai"]
    glm = dict(next(m for m in packaged["models"] if m["key"] == "glm-5.2:cloud"))
    glm["input_per_million"] = 9.99
    subset.append(glm)
    save_pricing_config({**packaged, "models": subset})

    merged = pricing_catalog()
    assert merged["using_override"] is True
    keys = {(m["provider"], m["key"]) for m in merged["models"]}
    # Packaged rows the override never mentioned are present...
    assert ("anthropic", "claude-opus-5") in keys
    assert ("ollama", "glm-5.3:cloud") in keys
    assert ("gemini", "gemini-3.6-flash") in keys
    # ...and the operator's explicit rate still wins for rows it defines.
    assert resolve_token_pricing("ollama", "glm-5.2").input_per_million == pytest.approx(9.99)
    assert resolve_token_pricing("anthropic", "claude-opus-5").input_per_million == pytest.approx(5.0)
    appended = next(m for m in merged["models"] if m["key"] == "claude-opus-5")
    assert "not in operator override" in appended["source"]
    # No duplicates by (provider, key)
    assert len(keys) == len(merged["models"])
