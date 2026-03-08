import unittest

from mu_cli.cli import CommandCompleter, _handle_local_command, build_help_text
from mu_cli.tools.filesystem import ReadFileTool


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = [ReadFileTool()]

    def test_build_help_text_includes_tool_description_and_args(self) -> None:
        help_text = build_help_text(self.tools)
        self.assertIn("/tool-help <name>", help_text)
        self.assertIn("read_file", help_text)
        self.assertIn("path (required)", help_text)

    def test_tool_help_command_returns_tooltip(self) -> None:
        handled, output = _handle_local_command("/tool-help read_file", self.tools)
        self.assertTrue(handled)
        assert output is not None
        self.assertIn("Read a UTF-8 text file from disk", output)

    def test_command_completion(self) -> None:
        completer = CommandCompleter(self.tools)
        self.assertIn("/help", completer.matches("/h", "/h"))
        self.assertIn("read_file", completer.matches("re", "/tool re"))


if __name__ == "__main__":
    unittest.main()
