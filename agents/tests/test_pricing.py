import tempfile
import unittest
from pathlib import Path

from mu_cli.core.types import UsageStats
from mu_cli.pricing import PricingCatalog


class PricingTests(unittest.TestCase):
    def test_creates_default_config_and_estimates_cost(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "pricing.json"
            catalog = PricingCatalog(cfg)
            report = catalog.estimate_cost(
                provider="openai",
                model="gpt-4o-mini",
                usage=UsageStats(input_tokens=1000, output_tokens=500, total_tokens=1500),
            )
            self.assertTrue(cfg.exists())
            self.assertGreaterEqual(report.estimated_cost_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
