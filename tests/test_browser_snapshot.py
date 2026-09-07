"""Unit tests for the browser_snapshot agent tool."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from mu.tools.agent import browser


def test_resolve_target_rejects_empty():
    url, err = browser._resolve_target("")
    assert url is None
    assert err and "requires" in err


def test_resolve_target_local_file(tmp_path):
    f = tmp_path / "page.html"
    f.write_text("<html></html>")
    url, err = browser._resolve_target(str(f))
    assert err is None
    assert url == "file://" + str(f)


def test_resolve_target_http_passthrough():
    # URL validation now resolves DNS for the SSRF gate. Supply a public
    # answer explicitly so this unit test does not require internet access.
    with patch.object(socket, "getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0)),
    ]) as resolve:
        url, err = browser._resolve_target("https://example.com")
    resolve.assert_called_once_with("example.com", None)
    assert err is None
    assert url == "https://example.com"


@pytest.mark.parametrize("addresses", [[], ["127.0.0.1"], ["93.184.215.14", "10.0.0.1"]])
def test_resolve_target_requires_public_dns_answers(addresses):
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
        for address in addresses
    ]
    with patch.object(socket, "getaddrinfo", return_value=answers):
        url, err = browser._resolve_target("https://example.com")
    assert url is None
    assert err


def test_resolve_target_fails_closed_when_dns_is_unavailable():
    with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("DNS unavailable")):
        url, err = browser._resolve_target("https://example.com")
    assert url is None
    assert "cannot resolve hostname" in err


def test_resolve_target_missing_path(tmp_path):
    url, err = browser._resolve_target(str(tmp_path / "nope.html"))
    assert url is None
    assert "not found" in err


def test_resolve_target_directory_rejected(tmp_path):
    url, err = browser._resolve_target(str(tmp_path))
    assert url is None
    assert "directory" in err


def test_handler_rejects_invalid_target():
    result = browser.browser_snapshot({"url": ""}, context=None)
    assert result["ok"] is False
    assert result["error_code"] == "invalid_target"


def test_tool_registered_in_descriptors():
    from mu.tools.descriptors import TOOLS

    assert any(t.name == "browser_snapshot" for t in TOOLS)
