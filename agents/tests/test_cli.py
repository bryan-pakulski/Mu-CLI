import tempfile
import unittest
from pathlib import Path

from mu_cli.cli import (
    CommandCompleter,
    RuntimeContext,
    _format_models,
    _handle_local_command,
    build_help_text,
)
from mu_cli.policy import ApprovalPolicy
from mu_cli.pricing import PricingCatalog
from mu_cli.session import SessionStore
from mu_cli.tools.filesystem import ReadFileTool
from mu_cli.workspace import WorkspaceStore


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = [ReadFileTool()]
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.store = WorkspaceStore(root / "workspaces")
        self.context = RuntimeContext(
            provider_name="echo",
            model_name="echo",
            api_key=None,
            workspace_store=self.store,
            tools=self.tools,
            approval_policy=ApprovalPolicy(mode="ask"),
            pricing=PricingCatalog(root / "pricing.json"),
            session_store=SessionStore(root / "sessions", "test"),
            workspace_path=None,
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_build_help_text_includes_tool_description_and_args(self) -> None:
        help_text = build_help_text(self.tools)
        self.assertIn("/tool-help <name>", help_text)
        self.assertIn("read_file", help_text)
        self.assertIn("path (required)", help_text)

    def test_tool_help_command_returns_tooltip(self) -> None:
        handled, output, replacement = _handle_local_command("/tool-help read_file", self.context, None)
        self.assertTrue(handled)
        self.assertIsNone(replacement)
        assert output is not None
        self.assertIn("Read a UTF-8 text file from disk", output)

    def test_model_catalog(self) -> None:
        text = _format_models("openai")
        self.assertIn("gpt-4o-mini", text)

    def test_command_completion(self) -> None:
        completer = CommandCompleter(self.tools)
        self.assertIn("/help", completer.matches("/h", "/h"))
        self.assertIn("read_file", completer.matches("re", "/tool re"))
        self.assertIn("attach", completer.matches("a", "/workspace a"))


if __name__ == "__main__":
    unittest.main()
