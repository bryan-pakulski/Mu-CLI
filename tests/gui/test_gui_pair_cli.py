"""``mucli gui pair`` prints the QR and the pairing code, and exits cleanly."""

from __future__ import annotations

import io
import time

from rich.console import Console

from mu.gui import devices_cli
import mu.ui.theme as theme


def test_pair_prints_the_code_after_the_qr(monkeypatch, capsys):
    pairing = {
        "payload": "mucli://pair?code=ABCD2345EFGH",
        "display": "ABCD-2345-EFGH",
        "expires_at": time.time() + 600,
        "urls": ["http://192.168.1.5:30311"],
        "listening_on_loopback": False,
    }
    monkeypatch.setattr(devices_cli, "_call", lambda *a, **k: pairing)
    out = io.StringIO()
    monkeypatch.setattr(theme, "get_console", lambda: Console(file=out, width=200, color_system=None))

    assert devices_cli.main(["pair"]) == 0

    text = out.getvalue()
    assert "ABCD-2345-EFGH" in text
    assert "valid 10 min" in text or "valid 9 min" in text
    assert "http://192.168.1.5:30311" in text
