import tempfile
import unittest
from pathlib import Path

from mu_cli.tools.filesystem import (
    GetWorkspaceFileContextTool,
    ListWorkspaceFilesTool,
    WriteFileTool,
)
from mu_cli.workspace import WorkspaceStore


class WorkspaceToolsTests(unittest.TestCase):
    def test_workspace_tools(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            (root / "a.py").write_text("print('a')\n", encoding="utf-8")

            store = WorkspaceStore(Path(td) / "store")
            store.attach(root)

            list_tool = ListWorkspaceFilesTool(store)
            get_tool = GetWorkspaceFileContextTool(store)

            listed = list_tool.run({"query": "a.py"})
            self.assertTrue(listed.ok)
            self.assertIn("a.py", listed.output)

            ctx = get_tool.run({"path": "a.py", "max_chars": 20})
            self.assertTrue(ctx.ok)
            self.assertIn("print('a')", ctx.output)

    def test_write_file_tool(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tool = WriteFileTool()
            target = Path(td) / "x" / "file.txt"
            result = tool.run({"path": str(target), "content": "hello"})
            self.assertTrue(result.ok)
            self.assertEqual("hello", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
