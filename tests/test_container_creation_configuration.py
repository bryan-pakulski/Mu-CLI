from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from mu.container.docker_cli import CommandRunner
from mu.container.network import create_isolated_network
from mu.container.ref import ContainerRef
from mu.container.tui import _domain_list, configure_tui_container
from mu.gui.routers.containers import container_defaults

ROOT = Path(__file__).resolve().parents[1]


def test_container_defaults_include_editable_worker_template():
    payload = asyncio.run(container_defaults())
    assert payload["dockerfile"].startswith("FROM ubuntu:24.04")
    assert 'ENTRYPOINT ["python3", "-m", "mu.container.worker"]' in payload["dockerfile"]
    assert "api.openai.com" in payload["egress_allow"]
    assert payload["egress_deny"] == []


def test_network_blocklist_takes_precedence_over_allowlist():
    runner = CommandRunner(dry_run=True)
    policy = create_isolated_network(
        "mucli-policy-net",
        ["api.openai.com", "ollama.com"],
        egress_deny=["OLLAMA.COM"],
        proxy_image="mucli/policy:test",
        runner=runner,
    )
    assert policy.allowed_domains == ["api.openai.com"]
    assert policy.denied_domains == ["ollama.com"]


def test_domain_list_normalizes_and_deduplicates():
    assert _domain_list(" API.OPENAI.COM,ollama.com\napi.openai.com ") == [
        "api.openai.com",
        "ollama.com",
    ]


def test_tui_can_select_an_existing_managed_container(monkeypatch):
    ref = ContainerRef(
        container_id="abc",
        name="mucli-shared",
        image="mucli/shared:test",
        dockerfile_hash="hash",
        egress_allow=["api.openai.com"],
        egress_deny=["telemetry.example.com"],
        network_name="mucli-shared-net",
        status="running",
    )
    registry = SimpleNamespace(list_containers=lambda: [ref])
    supervisor = SimpleNamespace(registry=registry)
    manager = SimpleNamespace(
        current_session_name="demo",
        container_config={},
        save_history=lambda _folders: None,
    )
    session = SimpleNamespace(
        session_manager=manager,
        folder_context=SimpleNamespace(folders=[]),
    )

    answers = iter(["existing", "mucli-shared"])
    monkeypatch.setattr(
        "mu.container.tui.prompt_choice",
        lambda *args, **kwargs: next(answers),
    )

    config = configure_tui_container(session, supervisor=supervisor)

    assert config["container_name"] == "mucli-shared"
    assert config["egress_allow"] == ["api.openai.com"]
    assert config["egress_deny"] == ["telemetry.example.com"]


def test_web_creation_uses_compact_expanding_editors():
    html = (ROOT / "mu/gui/templates/fragments/welcome.html").read_text()
    assert "Worker image template" in html
    assert "Egress policy" in html
    assert "container-editor-modal" in html
    assert "DEFAULT_CONTAINER_DOCKERFILE" in html
    assert "body.egress_deny" in html


def test_mobile_creation_uses_full_screen_editors():
    source = (ROOT / "mobile/android/src/components/NewSessionSheet.tsx").read_text()
    assert "Worker image template" in source
    assert "Allowlist and blocklist" in source
    assert "getContainerDefaults" in source
    assert "egressDeny: splitLines(egressDeny)" in source
    assert "visible={containerEditor !== null}" in source


def test_web_creation_uses_background_job_and_output_dropdown():
    html = (ROOT / "mu/gui/templates/fragments/welcome.html").read_text()
    assert 'background_container: this.form.sessionType === "container"' in html
    assert "container-creation-output" in html
    assert "Docker output" in html
    assert "waitForContainerCreation" in html


def test_command_output_redacts_environment_secrets():
    events: list[tuple[str, str]] = []
    runner = CommandRunner(dry_run=True)
    runner.run(
        ["docker", "create", "-e", "OLLAMA_API_KEY=do-not-leak", "image"],
        output_callback=lambda stream, text: events.append((stream, text)),
    )
    rendered = "\n".join(text for _stream, text in events)
    assert "do-not-leak" not in rendered
    assert "OLLAMA_API_KEY=<redacted>" in rendered
