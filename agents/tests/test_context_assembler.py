import unittest

from mu_cli.context_assembler import assemble_context_block
from mu_cli.core.types import Message, Role


class ContextAssemblerTests(unittest.TestCase):
    def test_assemble_context_block_contains_tiers_and_stats(self) -> None:
        messages = [
            Message(role=Role.SYSTEM, content="Skill guidance", metadata={"kind": "skill:review"}),
            Message(role=Role.USER, content="Investigate issue", metadata={}),
            Message(role=Role.ASSISTANT, content="Working on it", metadata={}),
        ]
        summary_index = [{"topics": "auth", "summary": "Refactored token checks"}]

        result = assemble_context_block(messages, summary_index, max_chars=1200)

        self.assertIn("Context memory snapshot", result.text)
        self.assertIn("Pinned instructions", result.text)
        self.assertIn("Active working memory", result.text)
        self.assertIn("Archived summaries", result.text)
        self.assertGreaterEqual(result.stats.get("pinned_count", 0), 1)
        self.assertGreaterEqual(result.stats.get("active_count", 0), 1)
        self.assertGreaterEqual(result.stats.get("archived_count", 0), 1)

    def test_assemble_context_block_enforces_budget(self) -> None:
        messages = [Message(role=Role.USER, content="x" * 5000, metadata={})]
        result = assemble_context_block(messages, [], max_chars=900)
        self.assertLessEqual(len(result.text), 900)
        self.assertLessEqual(result.stats.get("actual_chars", 0), 900)


if __name__ == "__main__":
    unittest.main()
