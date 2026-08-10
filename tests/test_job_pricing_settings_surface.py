from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_model_pricing_is_first_class_in_settings():
    product = (ROOT / "mu/gui/static/js/product.js").read_text(encoding="utf-8")
    css = (ROOT / "mu/gui/static/css/pricing_settings.css").read_text(encoding="utf-8")
    providers = (ROOT / "mu/gui/routers/providers.py").read_text(encoding="utf-8")

    assert "Alpine.store('pricingSettings'" in product
    assert "tab.textContent = 'pricing'" in product
    assert "Model pricing" in product
    assert "OpenAI" in product
    assert "Gemini" in product
    assert "Ollama" in product
    assert "Input / 1M" in product
    assert "Cached input / 1M" in product
    assert "Output / 1M" in product
    assert "token priced" in product
    assert "estimated token" in product
    assert "local / $0 API" in product
    assert "unpriced" in product
    assert "Advanced registry" in product

    assert "fetch('/api/providers/pricing'" in product
    assert "method: 'PUT'" in product
    assert "fetch('/api/providers/pricing/reset'" in product
    assert "method: 'POST'" in product

    assert '@router.get("/pricing")' in providers
    assert '@router.put("/pricing")' in providers
    assert '@router.post("/pricing/reset")' in providers

    assert ".pricing-rate-grid" in css
    assert ".pricing-provider-filter" in css
    assert ".pricing-settings-actions" in css


def test_pricing_settings_preserve_local_ollama_cost_semantics():
    product = (ROOT / "mu/gui/static/js/product.js").read_text(encoding="utf-8")
    pricing = (ROOT / "utils/model_pricing.py").read_text(encoding="utf-8")

    assert "Local Ollama remains $0 attributable provider/API cost" in product
    assert '"billing": "local"' in pricing
    assert '"api_cost_usd": 0.0' in pricing
    assert "host compute is excluded" in pricing
