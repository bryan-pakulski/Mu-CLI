import tempfile
import unittest
from pathlib import Path

from mu_cli.core.types import Message, Role
from mu_cli.session import SessionState, SessionStore


class SessionTests(unittest.TestCase):
    def test_save_and_load_session(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = SessionStore(Path(td), "demo")
            state = SessionState(
                provider="echo",
                model="echo",
                workspace="/tmp/ws",
                approval_mode="auto",
                messages=[Message(role=Role.USER, content="hello")],
                usage_totals={"total_tokens": 42.0},
                turns=[{"session": "demo", "total_tokens": 42}],
                uploads=[{"name": "notes.txt", "path": "/tmp/notes.txt"}],
                research_artifacts={"visited_urls": ["https://example.com"]},
            )
            store.save(state)

            loaded = store.load()
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual("echo", loaded.provider)
            self.assertEqual("hello", loaded.messages[0].content)
            self.assertEqual(42.0, loaded.usage_totals["total_tokens"])
            self.assertEqual("demo", loaded.turns[0]["session"])
            self.assertEqual("notes.txt", loaded.uploads[0]["name"])
            self.assertEqual("https://example.com", loaded.research_artifacts["visited_urls"][0])

    def test_list_and_delete_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = SessionStore(Path(td), "one")
            state = SessionState(
                provider="echo",
                model="echo",
                workspace=None,
                approval_mode="ask",
                messages=[],
            )
            store.save(state)
            store.use("two")
            store.save(state)

            sessions = store.list_sessions()
            self.assertEqual(["one", "two"], sessions)
            self.assertTrue(store.delete("one"))
            self.assertFalse(store.delete("missing"))


if __name__ == "__main__":
    unittest.main()
