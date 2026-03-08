from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mu_cli.core.types import UsageStats


DEFAULT_PRICING = {
    "openai": {
        "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    },
    "gemini": {
        "gemini-2.0-flash": {"input_per_1m": 0.10, "output_per_1m": 0.40},
    },
    "echo": {
        "echo": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    },
}


@dataclass(slots=True)
class TurnCostReport:
    provider: str
    model: str
    usage: UsageStats
    estimated_cost_usd: float


class PricingCatalog:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.data = self._load_or_create()

    def _load_or_create(self) -> dict:
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(DEFAULT_PRICING, indent=2), encoding="utf-8")
            return DEFAULT_PRICING
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def update_model_pricing(self, provider: str, model: str, input_per_1m: float, output_per_1m: float) -> None:
        provider_cfg = self.data.setdefault(provider, {})
        provider_cfg[model] = {
            "input_per_1m": float(input_per_1m),
            "output_per_1m": float(output_per_1m),
        }
        self.save()

    def estimate_cost(self, provider: str, model: str, usage: UsageStats) -> TurnCostReport:
        provider_cfg = self.data.get(provider, {})
        model_cfg = provider_cfg.get(model, {"input_per_1m": 0.0, "output_per_1m": 0.0})

        input_cost = (usage.input_tokens / 1_000_000) * float(model_cfg.get("input_per_1m", 0.0))
        output_cost = (usage.output_tokens / 1_000_000) * float(model_cfg.get("output_per_1m", 0.0))
        return TurnCostReport(
            provider=provider,
            model=model,
            usage=usage,
            estimated_cost_usd=input_cost + output_cost,
        )


def estimate_tokens(text: str) -> int:
    # Cheap local estimate for providers that do not return official usage.
    return max(1, len(text) // 4)
