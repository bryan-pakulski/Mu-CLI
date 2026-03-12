import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
ASGITransport = httpx.ASGITransport
AsyncClient = httpx.AsyncClient
create_app = __import__("server.app.main", fromlist=["create_app"]).create_app


@pytest.mark.asyncio
async def test_session_job_lifecycle_and_providers() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_session = await client.post(
            "/sessions",
            json={"workspace_path": "/tmp/work", "mode": "interactive"},
        )
        assert create_session.status_code == 200
        session = create_session.json()

        providers = await client.get("/providers")
        assert providers.status_code == 200
        provider_names = {item["name"] for item in providers.json()}
        assert "ollama" in provider_names
        assert "mock" in provider_names

        provider_models = await client.get("/providers/mock/models")
        assert provider_models.status_code == 200
        assert "mock-default" in provider_models.json()

        policies = await client.get("/policy-profiles")
        assert policies.status_code == 200
        assert "default" in policies.json()

        tools = await client.get("/tools")
        assert tools.status_code == 200
        assert any(t["name"] == "shell.exec" for t in tools.json())

        create_job = await client.post(
            f"/sessions/{session['id']}/jobs",
            json={"goal": "Create scaffold"},
        )
        assert create_job.status_code == 200
        job = create_job.json()

        await asyncio.sleep(0.25)

        fetched_job = await client.get(f"/jobs/{job['id']}")
        assert fetched_job.status_code == 200
        assert fetched_job.json()["state"] in {"running", "completed"}

        session_events = await client.get(f"/sessions/{session['id']}/events")
        assert session_events.status_code == 200
        assert any(item["event_type"] == "job_state" for item in session_events.json())


@pytest.mark.asyncio
async def test_cancel_and_resume_job_flow() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_session = await client.post(
            "/sessions",
            json={"workspace_path": "/tmp/work", "mode": "research"},
        )
        session = create_session.json()

        create_job = await client.post(
            f"/sessions/{session['id']}/jobs",
            json={"goal": "Run and cancel"},
        )
        job = create_job.json()

        cancel_response = await client.post(f"/jobs/{job['id']}/cancel")
        assert cancel_response.status_code == 200

        await asyncio.sleep(0.2)

        after_cancel = await client.get(f"/jobs/{job['id']}")
        assert after_cancel.status_code == 200
        assert after_cancel.json()["state"] in {"running", "cancelled", "completed"}

        if after_cancel.json()["state"] == "cancelled":
            resume_response = await client.post(f"/jobs/{job['id']}/resume")
            assert resume_response.status_code == 200
            await asyncio.sleep(0.25)
            after_resume = await client.get(f"/jobs/{job['id']}")
            assert after_resume.status_code == 200
            assert after_resume.json()["state"] in {"running", "completed", "failed"}


@pytest.mark.asyncio
async def test_session_pause_resume_terminate_and_events() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_session = await client.post(
            "/sessions",
            json={"workspace_path": "/tmp/work", "mode": "debugging"},
        )
        session = create_session.json()

        pause_response = await client.post(f"/sessions/{session['id']}/pause")
        assert pause_response.status_code == 200
        assert pause_response.json()["status"] == "paused"

        should_fail_job = await client.post(
            f"/sessions/{session['id']}/jobs",
            json={"goal": "should fail while paused"},
        )
        assert should_fail_job.status_code == 400

        resume_response = await client.post(f"/sessions/{session['id']}/resume")
        assert resume_response.status_code == 200
        assert resume_response.json()["status"] == "active"

        create_job = await client.post(
            f"/sessions/{session['id']}/jobs",
            json={"goal": "collect events"},
        )
        assert create_job.status_code == 200
        job = create_job.json()

        await asyncio.sleep(0.2)
        events_response = await client.get(f"/jobs/{job['id']}/events")
        assert events_response.status_code == 200
        assert len(events_response.json()) >= 1

        terminate_response = await client.post(f"/sessions/{session['id']}/terminate")
        assert terminate_response.status_code == 200
        assert terminate_response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_policy_approval_flow() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_session = await client.post(
            "/sessions",
            json={"workspace_path": "/tmp/work", "mode": "interactive"},
        )
        session = create_session.json()

        eval_policy = await client.get(
            "/policies/evaluate/shell.exec",
            params={"session_mode": "interactive"},
        )
        assert eval_policy.status_code == 200
        assert eval_policy.json()["decision"] == "ask"

        create_job = await client.post(
            f"/sessions/{session['id']}/jobs",
            json={"goal": "risky tool run", "constraints": {"tool_name": "shell.exec"}},
        )
        assert create_job.status_code == 200
        job = create_job.json()

        await asyncio.sleep(0.2)
        approvals = await client.get(f"/jobs/{job['id']}/approvals")
        assert approvals.status_code == 200
        assert len(approvals.json()) >= 1
        approval_id = approvals.json()[0]["id"]

        decision = await client.post(
            f"/jobs/{job['id']}/approvals/{approval_id}",
            json={"decision": "approved"},
        )
        assert decision.status_code == 200


@pytest.mark.asyncio
async def test_workspace_index_and_skill_discovery_endpoints(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Test Workspace\n")
    (tmp_path / "app.py").write_text("print('hello')\n")
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Demo Skill\n")

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_session = await client.post(
            "/sessions",
            json={"workspace_path": str(tmp_path), "mode": "interactive"},
        )
        assert create_session.status_code == 200
        session = create_session.json()

        build = await client.post(f"/sessions/{session['id']}/workspace/index")
        assert build.status_code == 200
        assert build.json()["indexed_files"] >= 2

        indexed = await client.get(f"/sessions/{session['id']}/workspace/index")
        assert indexed.status_code == 200
        assert len(indexed.json()) >= 2

        refreshed = await client.post(f"/sessions/{session['id']}/workspace/index/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["indexed_files"] >= 2
        assert refreshed.json()["next_refresh_after_s"] >= 1

        skills = await client.get("/skills", params={"session_id": session["id"]})
        assert skills.status_code == 200
        assert any(item["name"] == "demo-skill" for item in skills.json())


@pytest.mark.asyncio
async def test_session_with_legacy_mock_preference_does_not_attempt_unknown_provider() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_session = await client.post(
            "/sessions",
            json={
                "workspace_path": "/tmp/work",
                "mode": "interactive",
                "provider_preferences": {"ordered": ["ollama", "mock"]},
            },
        )
        assert create_session.status_code == 200
        session = create_session.json()

        create_job = await client.post(
            f"/sessions/{session['id']}/jobs",
            json={"goal": "legacy provider preference compatibility"},
        )
        assert create_job.status_code == 200
        job = create_job.json()

        await asyncio.sleep(0.35)

        events_response = await client.get(f"/jobs/{job['id']}/events")
        assert events_response.status_code == 200
        log_payloads = [
            event["payload"]
            for event in events_response.json()
            if event["event_type"] == "log"
        ]
        assert all("Unknown provider: mock" not in str(payload) for payload in log_payloads)


@pytest.mark.asyncio
async def test_list_sessions_and_update_session_config() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/sessions",
            json={"workspace_path": "/tmp/work", "mode": "interactive"},
        )
        assert created.status_code == 200
        session = created.json()

        listed = await client.get("/sessions")
        assert listed.status_code == 200
        assert any(item["id"] == session["id"] for item in listed.json())

        updated = await client.patch(
            f"/sessions/{session['id']}",
            json={
                "mode": "research",
                "policy_profile": "strict",
                "provider_preferences": {"ordered": ["ollama"]},
            },
        )
        assert updated.status_code == 200
        assert updated.json()["mode"] == "research"
        assert updated.json()["policy_profile"] == "strict"


@pytest.mark.asyncio
async def test_gui_index_served() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/gui")
        assert response.status_code == 200
        assert "Mu-CLI Chat Console" in response.text


@pytest.mark.asyncio
async def test_clear_and_delete_session_endpoints() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/sessions",
            json={"workspace_path": "/tmp/work", "mode": "interactive"},
        )
        assert created.status_code == 200
        session = created.json()

        cleared = await client.post(f"/sessions/{session['id']}/clear")
        assert cleared.status_code == 200
        assert cleared.json()["context_state"]["messages"] == []

        deleted = await client.delete(f"/sessions/{session['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        missing = await client.get(f"/sessions/{session['id']}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_default_session_exists_and_custom_name_roundtrip() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/sessions")
        assert listed.status_code == 200
        assert any(item["name"] == "default" for item in listed.json())

        created = await client.post(
            "/sessions",
            json={"workspace_path": "/tmp/work", "mode": "interactive", "name": "planning"},
        )
        assert created.status_code == 200
        assert created.json()["name"] == "planning"


@pytest.mark.asyncio
async def test_session_context_isolation_across_sessions() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/sessions",
            json={"workspace_path": "/tmp/work", "mode": "interactive", "name": "first"},
        )
        second = await client.post(
            "/sessions",
            json={"workspace_path": "/tmp/work", "mode": "interactive", "name": "second"},
        )
        assert first.status_code == 200
        assert second.status_code == 200

        first_session = first.json()
        second_session = second.json()

        first_job = await client.post(
            f"/sessions/{first_session['id']}/jobs",
            json={"goal": "first goal"},
        )
        assert first_job.status_code == 200

        first_detail = await client.get(f"/sessions/{first_session['id']}")
        second_detail = await client.get(f"/sessions/{second_session['id']}")
        assert first_detail.status_code == 200
        assert second_detail.status_code == 200

        first_messages = first_detail.json()["context_state"]["messages"]
        second_messages = second_detail.json()["context_state"]["messages"]
        assert any(msg["content"] == "first goal" for msg in first_messages)
        assert all(msg["content"] != "first goal" for msg in second_messages)
