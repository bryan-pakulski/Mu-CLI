from __future__ import annotations

from types import SimpleNamespace

import pytest

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.runner import SessionJobRunner
from utils.model_pricing import (
    PRICING_VERSION,
    estimate_model_cost,
    pricing_catalog,
    resolve_token_pricing,
)


def test_openai_cached_tokens_are_not_double_charged():
    estimate = estimate_model_cost(
        provider="openai",
        model_name="gpt-5.6-terra",
        input_tokens=1_000_000,
        cached_tokens=200_000,
        output_tokens=100_000,
    )
    # 0.8M*$2.50 + 0.2M*$0.25 + 0.1M*$15 = $3.55
    assert estimate["pricing_key"] == "gpt-5.6-terra"
    assert estimate["api_cost_usd"] == pytest.approx(3.55)
    assert estimate["usage"]["uncached_input"] == 800_000
    assert estimate["usage"]["cached_input"] == 200_000


def test_openai_long_context_tier_and_specific_alias_win():
    long = estimate_model_cost(
        provider="openai",
        model_name="gpt-5.6-sol-2026-08-01",
        input_tokens=300_000,
        cached_tokens=100_000,
        output_tokens=50_000,
    )
    assert long["long_context_tier"] is True
    assert long["rates"]["input_per_million"] == 10.0
    assert long["rates"]["output_per_million"] == 45.0
    assert long["api_cost_usd"] == pytest.approx(4.35)

    mini = resolve_token_pricing("openai", "gpt-5.4-mini")
    assert mini is not None
    assert mini.key == "gpt-5.4-mini"
    assert mini.input_per_million == pytest.approx(0.75)


def test_gemini_pro_high_tier_and_reasoning_not_double_charged():
    estimate = estimate_model_cost(
        provider="gemini",
        model_name="models/gemini-3.1-pro-preview-customtools",
        input_tokens=250_000,
        cached_tokens=50_000,
        output_tokens=20_000,
        reasoning_tokens=10_000,
    )
    # 0.2M*$4 + 0.05M*$0.40 + 0.02M*$18 = $1.18.
    # Reasoning is informational: Gemini's priced output already includes it.
    assert estimate["pricing_key"] == "gemini-3.1-pro-preview"
    assert estimate["long_context_tier"] is True
    assert estimate["api_cost_usd"] == pytest.approx(1.18)
    assert estimate["usage"]["reasoning"] == 10_000


def test_ollama_local_zero_api_cost_cloud_plan_cost_unknown():
    local = estimate_model_cost(
        provider="ollama",
        model_name="qwen3-coder-next:latest",
        input_tokens=50_000,
        output_tokens=2_000,
        endpoint="http://localhost:11434",
    )
    assert local["billing"] == "local"
    assert local["api_cost_usd"] == 0.0
    assert local["catalog"]["context_window"] == 256_000

    cloud = estimate_model_cost(
        provider="ollama",
        model_name="glm-5.2:cloud",
        input_tokens=50_000,
        output_tokens=2_000,
        endpoint="https://ollama.com",
    )
    assert cloud["billing"] == "plan"
    assert cloud["api_cost_usd"] is None
    assert cloud["catalog"]["context_window"] == 976_000


def test_unknown_model_is_unpriced_not_free():
    estimate = estimate_model_cost(
        provider="openai",
        model_name="future-model-not-in-map",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert estimate["billing"] == "unknown"
    assert estimate["source"] == "unpriced"
    assert estimate["api_cost_usd"] is None


def test_public_catalog_is_versioned_and_covers_all_supported_providers():
    catalog = pricing_catalog()
    assert catalog["version"] == PRICING_VERSION
    assert {item["provider"] for item in catalog["models"]} == {"openai", "gemini"}
    assert catalog["ollama"]
    assert "provider/API cost" in catalog["provider_notes"]["ollama_local"]


class _Manager:
    def __init__(self):
        self.token_counts = {
            "input": 10,
            "output": 20,
            "total": 30,
            "cached": 0,
            "reasoning": 0,
            "total_cost": 99.0,  # deliberately bogus legacy baseline
        }


class _Session:
    def __init__(self):
        self.session_manager = _Manager()
        self.variables = {}
        self.provider = SimpleNamespace(
            name="openai",
            model_name="gpt-5.4-mini",
            BASE_URL="https://api.openai.com/v1",
        )


def test_durable_job_usage_result_persists_tokens_and_pricing_provenance(tmp_path):
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(
        title="Costed job",
        execution={
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "agent_mode": "default",
            "session_type": "workspace",
        },
    ))
    session = _Session()
    runner = SessionJobRunner(service, build_session_fn=lambda *a, **k: session, base_args=SimpleNamespace())
    before = runner._token_snapshot(session)
    session.session_manager.token_counts.update({
        "input": 1_000_010,
        "output": 100_020,
        "total": 1_100_030,
        "cached": 200_000,
        "reasoning": 25_000,
        "total_cost": 1234.0,
    })

    cost, result = runner._usage_result(job, session, before, {"status": "completed"})

    # 0.8M*$0.75 + 0.2M*$0.075 + 0.1M*$4.50 = $1.065
    assert cost == pytest.approx(1.065)
    assert result["tokens"] == {
        "input": 1_000_000,
        "output": 100_000,
        "total": 1_100_000,
        "cached": 200_000,
        "reasoning": 25_000,
    }
    record = result["cost"]
    assert record["pricing_version"] == PRICING_VERSION
    assert record["pricing_key"] == "gpt-5.4-mini"
    assert record["source"] == "pricing_map"
    assert record["api_cost_usd"] == pytest.approx(1.065)
    assert record["legacy_loop_cost_usd"] == pytest.approx(1135.0)
    # Dedicated job session lifetime accounting is repaired to mapped cost.
    assert session.session_manager.token_counts["total_cost"] == pytest.approx(100.065)
