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
            )
            store.save(state)

            loaded = store.load()
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual("echo", loaded.provider)
            self.assertEqual("hello", loaded.messages[0].content)


if __name__ == "__main__":
    unittest.main()
