"""Offline provider for the real Neovim/stdio integration smoke test."""

import argparse
import sys

from mu.editor.__main__ import serve
from mu.editor.service import EditorService
from providers.base import StreamEvent


class FakeProvider:
    def __init__(self):
        self.empty_replies = 0

    def stream(self, messages, **kwargs):
        if (
            "SIMULATE_EMPTY_REPLY" in (messages[-1].parts[0].text or "")
            and self.empty_replies < 2
        ):
            self.empty_replies += 1
            yield StreamEvent("done")
            return
        yield StreamEvent("text_delta", text="return 42")


parser = argparse.ArgumentParser()
parser.add_argument("--workspace", required=True)
args = parser.parse_args()
serve(
    args.workspace,
    sys.stdin,
    sys.stdout,
    lambda root: EditorService(root, lambda *a, **k: FakeProvider()),
)
