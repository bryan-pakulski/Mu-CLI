import unittest

from mu_cli.web import _budget_policy_for_runtime


class BudgetPolicyTests(unittest.TestCase):
    def test_budget_policy_has_safe_bounds(self) -> None:
        small = _budget_policy_for_runtime(5)
        self.assertGreaterEqual(small.max_runtime_s, 30)
        self.assertGreaterEqual(small.max_tokens, 1200)
        self.assertGreaterEqual(small.max_tool_calls, 4)
        self.assertEqual(2, small.max_replans)

    def test_budget_policy_scales_with_runtime(self) -> None:
        base = _budget_policy_for_runtime(60)
        larger = _budget_policy_for_runtime(600)
        self.assertGreaterEqual(larger.max_runtime_s, base.max_runtime_s)
        self.assertGreaterEqual(larger.max_tokens, base.max_tokens)
        self.assertGreaterEqual(larger.max_tool_calls, base.max_tool_calls)


if __name__ == "__main__":
    unittest.main()
