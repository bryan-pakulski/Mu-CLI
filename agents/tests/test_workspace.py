import json
import tempfile
import unittest
from pathlib import Path

from mu_cli.workspace import WorkspaceStore


class WorkspaceTests(unittest.TestCase):
    def test_attach_indexes_files_and_respects_gitignore_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
            (root / "kept.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "ignored.txt").write_text("ignored\n", encoding="utf-8")
            (root / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
            (root / "config.txt").write_text("password=foo\n", encoding="utf-8")
            tpl = root / "agents" / "mu_cli" / "templates"
            tpl.mkdir(parents=True)
            (tpl / "index.html").write_text("<html></html>\n", encoding="utf-8")

            store = WorkspaceStore(Path(td) / "store")
            snapshot = store.attach(root)

            paths = [item.path for item in snapshot.files]
            self.assertIn("kept.py", paths)
            self.assertNotIn("ignored.txt", paths)
            self.assertIn(".env", paths)
            self.assertIn("config.txt", paths)
            self.assertIn("agents/mu_cli/templates/index.html", paths)
            self.assertGreaterEqual(snapshot.index_stats.get("indexed", 0), 4)

    def test_tool_run_persists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            (root / "main.py").write_text("print('x')\n", encoding="utf-8")

            store = WorkspaceStore(Path(td) / "store")
            store.attach(root)
            store.record_tool_run("read_file", {"path": "main.py"}, "[ok] content", True)

            persisted = list((Path(td) / "store").glob("workspace_*.json"))
            self.assertEqual(1, len(persisted))
            payload = json.loads(persisted[0].read_text(encoding="utf-8"))
            self.assertEqual(1, len(payload["tool_runs"]))


if __name__ == "__main__":
    unittest.main()
