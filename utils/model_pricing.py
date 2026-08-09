"""Versioned model pricing + execution catalog for MuCLI.

The goal is *defensible estimated spend*, not false precision.  Token-priced
providers (OpenAI / Gemini) have explicit USD-per-million rates.  Ollama local
is marked as zero provider/API cost (hardware is deliberately not included),
while Ollama Cloud is marked plan/usage based until a stable public per-model
rate is available.

Every estimate carries its pricing version/key/rates so callers can persist
provenance with job telemetry.  Historical costs therefore remain explainable
after this registry changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Optional


PRICING_VERSION = "2026-08-09"
USD_PER_MILLION = 1_000_000.0


@dataclass(frozen=True)
class ModelPricing:
    provider: str
    key: str
    input_per_million: Optional[float]
    cached_input_per_million: Optional[float]
    output_per_million: Optional[float]
    billing: str = "token"  # token | local | plan | unknown
    aliases: tuple[str, ...] = ()
    context_window: Optional[int] = None
    long_context_cutoff: Optional[int] = None
    long_input_per_million: Optional[float] = None
    long_cached_input_per_million: Optional[float] = None
    long_output_per_million: Optional[float] = None
    role: str = ""
    notes: str = ""
    source: str = ""

    def public_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["aliases"] = list(self.aliases)
        return value


@dataclass(frozen=True)
class ModelCatalogEntry:
    provider: str
    key: str
    aliases: tuple[str, ...] = ()
    context_window: Optional[int] = None
    role: str = ""
    local_size: str = ""
    usage_tier: str = ""
    notes: str = ""
    source: str = ""

    def public_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["aliases"] = list(self.aliases)
        return value


# Prices are USD / 1M tokens, standard synchronous API list prices.
# Sources are retained as provenance for maintenance/debug export.  The product
# UI should display PRICING_VERSION and treat these as estimates.
PRICING: tuple[ModelPricing, ...] = (
    # OpenAI — current GPT-5.6 family + useful recent compatibility models.
    ModelPricing(
        "openai", "gpt-5.6-sol", 5.00, 0.50, 30.00,
        aliases=("gpt-5.6", "gpt-5.6-sol"), context_window=1_050_000,
        long_context_cutoff=272_000,
        long_input_per_million=10.00, long_cached_input_per_million=1.00,
        long_output_per_million=45.00,
        role="frontier reasoning / hardest engineering work",
        notes="Prompts above 272K use long-context pricing; estimate applies the published 2x input / 1.5x output multiplier.",
        source="https://openai.com/api/pricing/",
    ),
    ModelPricing(
        "openai", "gpt-5.6-terra", 2.50, 0.25, 15.00,
        aliases=("gpt-5.6-terra",), context_window=1_050_000,
        long_context_cutoff=272_000,
        long_input_per_million=5.00, long_cached_input_per_million=0.50,
        long_output_per_million=22.50,
        role="balanced agentic engineering",
        notes="Prompts above 272K use long-context pricing.",
        source="https://openai.com/api/pricing/",
    ),
    ModelPricing(
        "openai", "gpt-5.6-luna", 1.00, 0.10, 6.00,
        aliases=("gpt-5.6-luna",), context_window=1_050_000,
        long_context_cutoff=272_000,
        long_input_per_million=2.00, long_cached_input_per_million=0.20,
        long_output_per_million=9.00,
        role="high-volume / lower-cost agent work",
        notes="Prompts above 272K use long-context pricing.",
        source="https://openai.com/api/pricing/",
    ),
    ModelPricing(
        "openai", "gpt-5.5", 5.00, 0.50, 30.00,
        aliases=("gpt-5.5",), context_window=1_050_000,
        long_context_cutoff=272_000,
        long_input_per_million=10.00, long_cached_input_per_million=1.00,
        long_output_per_million=45.00,
        role="recent frontier compatibility",
        source="https://platform.openai.com/docs/models/gpt-5.5",
    ),
    ModelPricing(
        "openai", "gpt-5.4", 2.50, 0.25, 15.00,
        aliases=("gpt-5.4",), context_window=1_050_000,
        long_context_cutoff=272_000,
        long_input_per_million=5.00, long_cached_input_per_million=0.50,
        long_output_per_million=22.50,
        role="recent general reasoning compatibility",
        source="https://platform.openai.com/docs/models/gpt-5.4",
    ),
    ModelPricing(
        "openai", "gpt-5.4-mini", 0.75, 0.075, 4.50,
        aliases=("gpt-5.4-mini",), context_window=400_000,
        role="cheap subagents / verification / routine implementation",
        source="https://platform.openai.com/docs/models/gpt-5.4-mini",
    ),
    ModelPricing(
        "openai", "gpt-5.4-nano", 0.20, 0.02, 1.25,
        aliases=("gpt-5.4-nano",), context_window=400_000,
        role="classification / summaries / very cheap support tasks",
        source="https://platform.openai.com/docs/models/gpt-5.4-nano",
    ),
    ModelPricing(
        "openai", "gpt-5.2", 1.75, 0.175, 14.00,
        aliases=("gpt-5.2",), context_window=400_000,
        role="historical compatibility",
        source="https://platform.openai.com/docs/models/gpt-5.2",
    ),

    # Google Gemini — output rates include thinking tokens where applicable.
    ModelPricing(
        "gemini", "gemini-3.6-flash", 1.50, 0.15, 7.50,
        aliases=("gemini-3.6-flash",),
        role="fast agentic engineering / production default candidate",
        notes="Text baseline. Output price includes thinking tokens.",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    ModelPricing(
        "gemini", "gemini-3.5-flash", 1.50, 0.15, 9.00,
        aliases=("gemini-3.5-flash",),
        role="general fast agent work",
        notes="Text baseline. Output price includes thinking tokens.",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    ModelPricing(
        "gemini", "gemini-3.5-flash-lite", 0.30, 0.03, 2.50,
        aliases=("gemini-3.5-flash-lite",),
        role="high-throughput inexpensive support work",
        notes="Text baseline. Output price includes thinking tokens.",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    ModelPricing(
        "gemini", "gemini-3.1-pro-preview", 2.00, 0.20, 12.00,
        aliases=("gemini-3.1-pro-preview", "gemini-3.1-pro-preview-customtools"),
        long_context_cutoff=200_000,
        long_input_per_million=4.00, long_cached_input_per_million=0.40,
        long_output_per_million=18.00,
        role="high-capability reasoning / long-context engineering",
        notes="Higher tier applies above 200K input tokens. Output price includes thinking tokens.",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    ModelPricing(
        "gemini", "gemini-3.1-flash-lite", 0.25, 0.025, 1.50,
        aliases=("gemini-3.1-flash-lite",),
        role="lowest-cost Gemini text support work",
        notes="Text/image/video input baseline; audio has a different input rate.",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    # Legacy entries retained because existing MuCLI sessions/jobs may still use them.
    ModelPricing(
        "gemini", "gemini-2.5-pro", 1.25, None, 10.00,
        aliases=("gemini-2.5-pro",), long_context_cutoff=200_000,
        long_input_per_million=2.50, long_output_per_million=15.00,
        role="legacy compatibility",
        notes="Legacy baseline retained from the previous MuCLI pricing map.",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    ModelPricing(
        "gemini", "gemini-2.5-flash", 0.30, None, 2.50,
        aliases=("gemini-2.5-flash",),
        role="legacy compatibility",
        notes="Legacy baseline retained from the previous MuCLI pricing map.",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
)


# Ollama's useful baseline is catalog metadata rather than fabricated token
# prices. `estimate_model_cost` decides local-vs-cloud billing from endpoint /
# mode / model suffix.  These entries make the product's model economics UI
# useful even when cost is zero/plan-based.
OLLAMA_CATALOG: tuple[ModelCatalogEntry, ...] = (
    ModelCatalogEntry(
        "ollama", "glm-5.2:cloud", aliases=("glm-5.2", "glm-5.2:cloud"),
        context_window=976_000, role="flagship long-horizon agentic work",
        usage_tier="high", notes="Cloud-hosted; tools + thinking.",
        source="https://ollama.com/library/glm-5.2",
    ),
    ModelCatalogEntry(
        "ollama", "kimi-k2.7-code:cloud", aliases=("kimi-k2.7-code", "kimi-k2.7-code:cloud"),
        context_window=256_000, role="long-horizon coding agent",
        usage_tier="high", notes="Cloud-hosted coding-focused model.",
        source="https://ollama.com/library/kimi-k2.7-code",
    ),
    ModelCatalogEntry(
        "ollama", "qwen3-coder-next", aliases=("qwen3-coder-next",),
        context_window=256_000, role="local coding / tool-use agent",
        local_size="~52GB Q4", notes="80B total / ~3B active; tools.",
        source="https://ollama.com/library/qwen3-coder-next",
    ),
    ModelCatalogEntry(
        "ollama", "devstral-small-2", aliases=("devstral-small-2",),
        context_window=384_000, role="local software-engineering agent",
        local_size="~15GB", notes="Coding/tool model; cloud variant may expose a different context ceiling.",
        source="https://ollama.com/library/devstral-small-2",
    ),
    ModelCatalogEntry(
        "ollama", "gpt-oss:20b", aliases=("gpt-oss:20b", "gpt-oss-20b"),
        context_window=128_000, role="local reasoning + tools",
        local_size="~14GB", notes="Local and cloud variants available.",
        source="https://ollama.com/library/gpt-oss",
    ),
    ModelCatalogEntry(
        "ollama", "gpt-oss:120b", aliases=("gpt-oss:120b", "gpt-oss-120b"),
        context_window=128_000, role="large local reasoning + tools",
        local_size="~65GB", notes="Local and cloud variants available.",
        source="https://ollama.com/library/gpt-oss",
    ),
)


def _normalise(value: str) -> str:
    return str(value or "").strip().lower().replace("models/", "")


def infer_provider(model_name: str) -> str:
    name = _normalise(model_name)
    if name.startswith("gemini-"):
        return "gemini"
    if name.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    return ""


def _matches(model_name: str, candidate: str) -> bool:
    """Match stable names plus dated/snapshot suffixes without collisions."""
    model = _normalise(model_name)
    key = _normalise(candidate)
    if not model or not key:
        return False
    if model == key:
        return True
    # API snapshot names normally append `-YYYY...`; Ollama appends `:tag`.
    return model.startswith(key + "-") or model.startswith(key + ":")


def resolve_token_pricing(provider: str, model_name: str) -> Optional[ModelPricing]:
    provider_name = _normalise(provider) or infer_provider(model_name)
    matches: list[tuple[int, ModelPricing]] = []
    for item in PRICING:
        if provider_name and item.provider != provider_name:
            continue
        for alias in (item.key, *item.aliases):
            if _matches(model_name, alias):
                matches.append((len(alias), item))
                break
    if not matches:
        return None
    # Longest alias wins (e.g. gpt-5.4-mini before gpt-5.4).
    return max(matches, key=lambda pair: pair[0])[1]


def resolve_ollama_catalog(model_name: str) -> Optional[ModelCatalogEntry]:
    matches: list[tuple[int, ModelCatalogEntry]] = []
    for item in OLLAMA_CATALOG:
        for alias in (item.key, *item.aliases):
            if _matches(model_name, alias):
                matches.append((len(alias), item))
                break
    return max(matches, key=lambda pair: pair[0])[1] if matches else None


def ollama_billing_mode(*, model_name: str, mode: str = "", endpoint: str = "") -> str:
    """Return `local` or `plan` for an Ollama call without inventing rates."""
    model = _normalise(model_name)
    selected_mode = _normalise(mode)
    host = _normalise(endpoint)
    if selected_mode == "cloud" or model.endswith(":cloud") or "ollama.com" in host:
        return "plan"
    if selected_mode == "local":
        return "local"
    # In auto mode a custom non-ollama.com host/localhost is a daemon.
    if host:
        return "plan" if "ollama.com" in host else "local"
    return "local"


def estimate_model_cost(
    *,
    provider: str,
    model_name: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
    provider_reported_cost: Optional[float] = None,
    ollama_mode: str = "",
    endpoint: str = "",
) -> Dict[str, Any]:
    """Estimate one model call and return a persistence-ready ledger record.

    `input_tokens` is treated as the provider's total prompt count, including
    cached tokens when the provider reports them that way.  Cached prompt tokens
    are therefore removed from ordinary input before charging the cheaper cache
    rate. Reasoning/thinking tokens are informational here because both OpenAI's
    completion count and Gemini's priced output already include them.
    """
    provider_name = _normalise(provider) or infer_provider(model_name) or "unknown"
    model = str(model_name or "")
    in_tokens = max(0, int(input_tokens or 0))
    out_tokens = max(0, int(output_tokens or 0))
    cache_tokens = max(0, min(in_tokens, int(cached_tokens or 0)))
    reasoning = max(0, int(reasoning_tokens or 0))
    uncached_tokens = max(0, in_tokens - cache_tokens)

    base: Dict[str, Any] = {
        "pricing_version": PRICING_VERSION,
        "provider": provider_name,
        "model": model,
        "usage": {
            "input": in_tokens,
            "uncached_input": uncached_tokens,
            "cached_input": cache_tokens,
            "output": out_tokens,
            "reasoning": reasoning,
        },
    }

    if provider_reported_cost is not None:
        base.update({
            "pricing_key": "provider-reported",
            "billing": "token",
            "source": "provider_reported",
            "api_cost_usd": max(0.0, float(provider_reported_cost)),
            "rates": {},
        })
        return base

    if provider_name == "ollama":
        billing = ollama_billing_mode(model_name=model, mode=ollama_mode, endpoint=endpoint)
        catalog = resolve_ollama_catalog(model)
        base.update({
            "pricing_key": catalog.key if catalog else model,
            "billing": billing,
            "source": "local" if billing == "local" else "plan",
            "api_cost_usd": 0.0 if billing == "local" else None,
            "rates": {},
            "note": (
                "Local Ollama has $0 attributable provider/API cost; hardware/host compute is excluded."
                if billing == "local"
                else "Ollama Cloud is plan/usage based in this baseline; no fabricated per-token rate is applied."
            ),
        })
        if catalog:
            base["catalog"] = catalog.public_dict()
        return base

    pricing = resolve_token_pricing(provider_name, model)
    if pricing is None:
        base.update({
            "pricing_key": "",
            "billing": "unknown",
            "source": "unpriced",
            "api_cost_usd": None,
            "rates": {},
            "note": "No pricing-map entry matched this model; cost intentionally remains unknown rather than $0.",
        })
        return base

    high = bool(pricing.long_context_cutoff and in_tokens > pricing.long_context_cutoff)
    input_rate = (
        pricing.long_input_per_million
        if high and pricing.long_input_per_million is not None
        else pricing.input_per_million
    )
    cached_rate = (
        pricing.long_cached_input_per_million
        if high and pricing.long_cached_input_per_million is not None
        else pricing.cached_input_per_million
    )
    output_rate = (
        pricing.long_output_per_million
        if high and pricing.long_output_per_million is not None
        else pricing.output_per_million
    )
    # If a legacy entry has no separate cache rate, conservatively use ordinary
    # input pricing rather than treating cached tokens as free.
    effective_cached_rate = cached_rate if cached_rate is not None else input_rate
    cost = 0.0
    if input_rate is not None:
        cost += uncached_tokens / USD_PER_MILLION * input_rate
    if effective_cached_rate is not None:
        cost += cache_tokens / USD_PER_MILLION * effective_cached_rate
    if output_rate is not None:
        cost += out_tokens / USD_PER_MILLION * output_rate

    base.update({
        "pricing_key": pricing.key,
        "billing": pricing.billing,
        "source": "pricing_map",
        "api_cost_usd": cost,
        "long_context_tier": high,
        "rates": {
            "input_per_million": input_rate,
            "cached_input_per_million": effective_cached_rate,
            "output_per_million": output_rate,
        },
        "source_url": pricing.source,
    })
    return base


def calculate_model_cost(**kwargs: Any) -> Optional[float]:
    """Convenience numeric API; unknown/plan pricing returns None."""
    value = estimate_model_cost(**kwargs).get("api_cost_usd")
    return None if value is None else float(value)


def pricing_catalog() -> Dict[str, Any]:
    """Return the versioned public catalog for GUI/mobile/TUI consumption."""
    return {
        "version": PRICING_VERSION,
        "currency": "USD",
        "unit": "per_million_tokens",
        "models": [item.public_dict() for item in PRICING],
        "ollama": [item.public_dict() for item in OLLAMA_CATALOG],
        "provider_notes": {
            "openai": "Token-priced list-rate estimate. Cached prompt tokens use the mapped cached-input rate.",
            "gemini": "Token-priced text baseline. Published output rates include thinking tokens.",
            "ollama_local": "$0 attributable provider/API cost; workstation/host/GPU electricity and compute are separate economics.",
            "ollama_cloud": "Plan/usage based baseline; no universal per-token rate is assumed.",
        },
    }


__all__ = [
    "ModelCatalogEntry",
    "ModelPricing",
    "OLLAMA_CATALOG",
    "PRICING",
    "PRICING_VERSION",
    "calculate_model_cost",
    "estimate_model_cost",
    "infer_provider",
    "ollama_billing_mode",
    "pricing_catalog",
    "resolve_ollama_catalog",
    "resolve_token_pricing",
]
