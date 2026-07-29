from __future__ import annotations

from pathlib import Path

from mu.container.egress_proxy import EgressPolicy, _parse_host_port


def test_proxy_policy_denies_before_allowing():
    policy = EgressPolicy(
        allow=("*.example.com", "api.openai.com", "203.0.113.8"),
        deny=("blocked.example.com",),
        host_allow={"host.docker.internal": (30311,)},
    )
    assert policy.permits("api.example.com", 443) is True
    assert policy.permits("blocked.example.com", 443) is False
    assert policy.permits("api.openai.com", 443) is True
    assert policy.permits("api.openai.com", 22) is False
    assert policy.permits("203.0.113.8", 443) is True
    assert policy.permits("203.0.113.9", 443) is False
    assert policy.permits("host.docker.internal", 30311) is True
    assert policy.permits("host.docker.internal", 22) is False


def test_proxy_target_parser_supports_ipv4_names_and_ipv6():
    assert _parse_host_port("api.openai.com:443", 80) == ("api.openai.com", 443)
    assert _parse_host_port("example.com", 80) == ("example.com", 80)
    assert _parse_host_port("[2001:db8::1]:8443", 443) == ("2001:db8::1", 8443)


def test_container_runtime_has_no_privileged_host_command_path():
    root = Path(__file__).resolve().parents[1] / "mu" / "container"
    commands_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.glob("*.py")
        if path.name != "egress_proxy.py"
    )
    assert 'require("sudo")' not in commands_source
    assert 'require("iptables")' not in commands_source
    assert '"DOCKER-USER"' not in commands_source


def test_proxy_forwards_only_explicit_control_plane_endpoint():
    import asyncio

    from mu.container.egress_proxy import _handle_client

    async def scenario():
        async def target(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            body = b"proxy-ok"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        target_server = await asyncio.start_server(target, "127.0.0.1", 0)
        target_port = target_server.sockets[0].getsockname()[1]
        policy = EgressPolicy(host_allow={"127.0.0.1": (target_port,)})
        proxy_server = await asyncio.start_server(
            lambda reader, writer: _handle_client(reader, writer, policy),
            "127.0.0.1",
            0,
        )
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(
                f"GET http://127.0.0.1:{target_port}/ok HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{target_port}\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            assert b"200 OK" in response
            assert response.endswith(b"proxy-ok")
        finally:
            proxy_server.close()
            target_server.close()
            await proxy_server.wait_closed()
            await target_server.wait_closed()

    asyncio.run(scenario())
