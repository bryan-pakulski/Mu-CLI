from pathlib import Path
from types import SimpleNamespace

from mu.container.supervisor import ContainerSupervisor


ROOT = Path(__file__).resolve().parents[1]


def test_web_welcome_has_three_primary_entries_and_guided_creation():
    html = (ROOT / "mu/gui/templates/fragments/welcome.html").read_text()
    # Wash W4 (G§9.18): the three choice cards are Continue, Start new and
    # Environments; the saved-sessions list keeps its "Sessions" title.
    assert "Sessions" in html
    assert ">Continue</strong>" in html
    assert ">Start new</strong>" in html
    assert ">Environments</strong>" in html
    assert "openSessions" in html
    assert "openCreate" in html
    assert "containerSource" in html
    assert "existingContainer" in html
    assert "Alpine.store('fileBrowser').show('folder'" in html


def test_container_manager_exposes_edit_clone_and_template_actions():
    html = (ROOT / "mu/gui/templates/containers.html").read_text()
    script = (ROOT / "mu/gui/static/js/containers.js").read_text()
    # Mu §4.1: the page is named once, by its noun, in the page header.
    assert "{% block page_title %}Environments{% endblock %}" in html
    assert "Folder mounts" in html
    assert "edit" in script
    assert "clone" in script
    assert "snapshot" in script
    assert "add-environment-mount" in html


def test_mobile_start_screen_and_wizard_match_web_structure():
    prompt = (ROOT / "mobile/android/src/components/SessionStartPrompt.tsx").read_text()
    wizard = (ROOT / "mobile/android/src/components/NewSessionSheet.tsx").read_text()
    manager = (ROOT / "mobile/android/src/components/ContainerManagerSheet.tsx").read_text()
    assert "Sessions" in prompt
    assert "Create new" in prompt
    assert "Container management" in prompt
    assert "existingContainer" in wizard
    assert "WorkspacePathField" in wizard
    assert "clone" in manager
    assert "snapshot" in manager
    assert "WorkspacePathField" in manager


def test_reconfigure_preserves_session_bindings(monkeypatch, tmp_path):
    supervisor = object.__new__(ContainerSupervisor)
    supervisor.registry = SimpleNamespace()
    supervisor.template_registry = SimpleNamespace()
    existing = SimpleNamespace(
        name="mucli-demo",
        attached_sessions=["one", "two"],
        standalone=False,
    )
    supervisor.registry.get = lambda name: existing
    supervisor.registry.upsert = lambda ref: calls.append(("upsert", ref))
    calls = []
    monkeypatch.setattr(
        supervisor,
        "_discard_stale_registration",
        lambda ref, preserve_volumes=True: calls.append(("discard", preserve_volumes)),
    )
    monkeypatch.setattr(
        supervisor,
        "create_environment",
        lambda **kwargs: calls.append(("create", kwargs)) or SimpleNamespace(name="mucli-demo", standalone=True),
    )
    monkeypatch.setattr(
        supervisor,
        "attach_session",
        lambda name, session_name: calls.append(("attach", name, session_name)) or SimpleNamespace(name=name),
    )

    result = supervisor.reconfigure_environment(
        "mucli-demo",
        dockerfile="FROM ubuntu:24.04",
        mounts=[],
        egress_allow=["example.com"],
        egress_deny=[],
        start=True,
    )

    assert result.name == "mucli-demo"
    assert calls[0] == ("discard", True)
    create_args = calls[1][1]
    assert create_args["container_name"] == "mucli-demo"
    assert ("attach", "mucli-demo", "one") in calls
    assert ("attach", "mucli-demo", "two") in calls


def test_existing_container_session_attaches_without_rebuilding(tmp_path, monkeypatch):
    import asyncio
    import threading
    from mu.gui.routers import sessions

    monkeypatch.setattr(sessions._config, "HISTORY_DIR", str(tmp_path))
    ref = SimpleNamespace(name="mucli-shared", to_dict=lambda include_secret=False: {"name": "mucli-shared"})
    attached: list[tuple[str, str]] = []
    configuration = {
        "container_name": "mucli-shared",
        "dockerfile": None,
        "template_name": "python-tools",
        "mounts": [],
        "egress_allow": ["api.openai.com"],
        "egress_deny": [],
    }
    supervisor = SimpleNamespace(
        resolve=lambda name: ref if name == "mucli-shared" else None,
        configuration=lambda _name: dict(configuration),
        attach_session=lambda name, session_name, **_kwargs: attached.append((name, session_name)) or ref,
        detach_session=lambda *args, **kwargs: None,
        container_for_session=lambda _name: ref,
    )
    state = SimpleNamespace(
        port=30311,
        container_supervisor=supervisor,
        container_creation_status={},
        container_creation_lock=threading.Lock(),
        container_creation_tasks={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    async def fake_load_session(name, request, payload=None):
        return {"ok": True, "name": name, "active": True}

    monkeypatch.setattr(sessions, "load_session", fake_load_session)

    result = asyncio.run(
        sessions.create_session(
            request,
            {
                "name": "attached-session",
                "provider": "openai",
                "model": "gpt-test",
                "session_type": "container",
                "container_source": "existing",
                "existing_container": "mucli-shared",
            },
        )
    )

    assert result["session_type"] == "container"
    assert attached == [("mucli-shared", "attached-session")]
    saved = __import__("json").loads(
        (tmp_path / "sessions" / "attached-session" / "session.json").read_text()
    )
    assert saved["container_config"]["container_name"] == "mucli-shared"


def test_container_management_allows_private_lan_but_shell_requires_loopback():
    from fastapi import HTTPException
    from mu.gui.routers.containers import _require_local_client

    private = SimpleNamespace(client=SimpleNamespace(host="192.168.1.25"))
    public = SimpleNamespace(client=SimpleNamespace(host="8.8.8.8"))
    loopback = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    _require_local_client(private)
    _require_local_client(loopback, allow_private_network=False)
    try:
        _require_local_client(private, allow_private_network=False)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("private LAN shell access should be rejected")
    try:
        _require_local_client(public)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("public management access should be rejected")


def test_file_browser_is_cloaked_and_above_nested_creation_modals():
    html = (ROOT / "mu/gui/templates/fragments/file_browser.html").read_text()
    from wash_css_helpers import css, decls
    overlays = css("patterns/overlays.css")
    modal = css("components/modal.css")
    welcome = (ROOT / "mu/gui/templates/fragments/welcome.html").read_text()
    assert 'x-show="$store.fileBrowser.open"' in html
    assert "x-cloak" in html
    # The browser backdrop stacks one step above every other .w-modal
    # (the guided welcome modal and the nested Docker/network editor).
    assert 'class="file-browser-backdrop w-modal"' in html
    assert decls(overlays, ".file-browser-backdrop.w-modal")["z-index"] == "calc(var(--wash-z-modal) + 10)"
    assert decls(modal, ".w-modal")["z-index"] == "var(--wash-z-modal)"
    assert 'class="welcome-modal-backdrop w-modal"' in welcome  # guided welcome modal
    assert 'class="container-editor-backdrop w-modal"' in welcome  # nested Docker/network editor


def test_environment_workspace_shows_list_main_and_context_side_by_side():
    # Mu §11b: the page is a list | main | aside workspace. The list holds the
    # environments then the templates; MAIN is the chosen environment itself (live
    # charts over a rolling sample buffer, the terminal inline) or the template's
    # editor; ASIDE is its context. The first environment is chosen on arrival
    # without writing the URL; a picked row (click, j/k) goes in ?open= so a reload
    # reopens it.
    html = (ROOT / "mu/gui/templates/containers.html").read_text()
    script = (ROOT / "mu/gui/static/js/containers.js").read_text()
    panes = html.split('<div class="w-panes w-env-panes" id="env-panes" data-layout="list-main-aside">', 1)[1]
    assert panes.index('data-pane="list"') < panes.index('id="container-list"') < panes.index('id="template-list"') \
        < panes.index('data-pane="main"') < panes.index('data-pane="aside"')
    assert '<section class="w-env-terminal-host" id="env-terminal-host"' in html and 'id="shell-modal"' in html
    assert "function autoSelect()" in script and "if (!selectFromUrl()) autoSelect();" in script
    assert "setOpenParam(kind === 'template' ? `template:${name}` : name);" in script
    # Live charts: a bounded rolling buffer; disk I/O is a rate between cumulative samples.
    assert "const HISTORY = 72;" in script and "if (series.length > HISTORY) series.splice(0, series.length - HISTORY);" in script
    assert "r: prev && dt ? Math.max(0, (br - prev.br) / dt) : 0," in script
    # The terminal moves between the main pane and the modal; one body, one socket.
    assert "const inlineShell = () => !window.matchMedia || window.matchMedia('(width >= 840px)').matches;" in script
    assert "target.appendChild(body);" in script
    # Narrow screens: list ↔ main; the aside opens on demand below 1200px.
    assert "panes().setAttribute('data-show', 'main');" in script and "data-env-back" in script
    assert "panes().setAttribute('data-aside', 'open')" in script
    # Hidden dialogs carry aria-modal statically: only a visible one blocks j/k.
    assert "el.getClientRects().length > 0" in script
    # No zero counts in the create flow's summaries (Mu §5.5).
    assert "'No outbound domains'" in script and "state.devices.length ? ` · ${state.devices.length} device" in script
