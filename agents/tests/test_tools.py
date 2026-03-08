import tempfile
import unittest
from unittest.mock import patch
import os
import subprocess
from pathlib import Path

from mu_cli.tools.filesystem import (
    ApplyPatchTool,
    ClearUploadedContextStoreTool,
    FetchUrlContextTool,
    CustomCommandTool,
    ExtractLinksContextTool,
    FetchPdfContextTool,
    GetUploadedContextFileTool,
    GetWorkspaceFileContextTool,
    ListUploadedContextFilesTool,
    ListWorkspaceFilesTool,
    SearchWebContextTool,
    SearchArxivPapersTool,
    WriteFileTool,
)
from mu_cli.workspace import WorkspaceStore


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False


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

    def test_uploaded_context_tools(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            session = "demo"
            session_dir = root / session
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "ctx.txt").write_text("hello store", encoding="utf-8")

            getter = lambda: session
            listed = ListUploadedContextFilesTool(root, getter).run({})
            self.assertTrue(listed.ok)
            self.assertIn("ctx.txt", listed.output)

            content = GetUploadedContextFileTool(root, getter).run({"name": "ctx.txt"})
            self.assertTrue(content.ok)
            self.assertIn("hello store", content.output)

            cleared = ClearUploadedContextStoreTool(root, getter).run({})
            self.assertTrue(cleared.ok)
            self.assertIn("Removed 1", cleared.output)


    def test_apply_patch_tool_accepts_fenced_and_escaped_diff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
            target = repo / "my_flow.py"
            target.write_text("def remove_outlier(cpu_flow, p=1.5):\n\treturn cpu_flow\n", encoding="utf-8")

            patch_text = r"""```diff
diff
--- a/my_flow.py
+++ b/my_flow.py
@@ -1,2 +1,2 @@
-def remove_outlier(cpu_flow, p=1.5):\n\treturn cpu_flow
+def remove_outlier(cpu_flow, p=2.0):\n\treturn cpu_flow
```"""

            tool = ApplyPatchTool()
            prev = Path.cwd()
            try:
                os.chdir(repo)
                result = tool.run({"patch": patch_text})
            finally:
                os.chdir(prev)

            self.assertTrue(result.ok, result.output)
            updated = target.read_text(encoding="utf-8")
            self.assertIn("p=2.0", updated)

    def test_write_file_tool_respects_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            tool = WriteFileTool(lambda: root)
            result = tool.run({"path": "nested/file.txt", "content": "workspace"})
            self.assertTrue(result.ok)
            self.assertEqual("workspace", (root / "nested" / "file.txt").read_text(encoding="utf-8"))

    def test_apply_patch_tool_respects_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            (root / "a.txt").write_text("old\n", encoding="utf-8")
            patch = """--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-old
+new
"""
            tool = ApplyPatchTool(lambda: root)
            result = tool.run({"patch": patch})
            self.assertTrue(result.ok, result.output)
            self.assertEqual("new\n", (root / "a.txt").read_text(encoding="utf-8"))

    def test_fetch_url_context_tool(self) -> None:
        html = b"<html><body><h1>Hello</h1><p>World</p></body></html>"
        with patch('urllib.request.urlopen', return_value=_FakeResponse(html, 'text/html')):
            result = FetchUrlContextTool().run({"url": "https://example.com"})
        self.assertTrue(result.ok)
        self.assertIn("Hello", result.output)
        self.assertIn("World", result.output)

    def test_search_web_context_tool_duckduckgo(self) -> None:
        payload = b'{"Heading":"Example","RelatedTopics":[{"Text":"Result one","FirstURL":"https://example.com/1"}]}'
        with patch('urllib.request.urlopen', return_value=_FakeResponse(payload, 'application/json')):
            result = SearchWebContextTool().run({"query": "example", "provider": "duckduckgo"})
        self.assertTrue(result.ok)
        self.assertIn("https://example.com/1", result.output)

    def test_custom_command_tool(self) -> None:
        tool = CustomCommandTool(
            name="echo_custom",
            description="Echo custom arg",
            command=["python", "-c", "print('tool:' + '{value}')"],
            mutating=False,
        )
        result = tool.run({"args": {"value": "ok"}})
        self.assertTrue(result.ok, result.output)
        self.assertIn("tool:ok", result.output)

    def test_extract_links_context_tool(self) -> None:
        html = b'<html><body><a href="/a">A</a><a href="https://example.com/b">B</a></body></html>'
        with patch('urllib.request.urlopen', return_value=_FakeResponse(html, 'text/html')):
            result = ExtractLinksContextTool().run({"url": "https://example.com/root"})
        self.assertTrue(result.ok)
        self.assertIn("https://example.com/a", result.output)
        self.assertIn("https://example.com/b", result.output)

    def test_search_arxiv_papers_tool(self) -> None:
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Sample Paper</title>
    <summary>Summary text.</summary>
    <link rel="alternate" href="https://arxiv.org/abs/1234.5678v1" />
    <link title="pdf" href="https://arxiv.org/pdf/1234.5678v1" type="application/pdf" />
  </entry>
</feed>'''
        with patch('urllib.request.urlopen', return_value=_FakeResponse(xml, 'application/atom+xml')):
            result = SearchArxivPapersTool().run({"query": "sample"})
        self.assertTrue(result.ok)
        self.assertIn("Sample Paper", result.output)
        self.assertIn("https://arxiv.org/abs/1234.5678v1", result.output)

    def test_fetch_pdf_context_tool_validates_url(self) -> None:
        result = FetchPdfContextTool().run({"url": "example.com/file.pdf"})
        self.assertFalse(result.ok)
        self.assertIn("url must start", result.output)


if __name__ == "__main__":
    unittest.main()
