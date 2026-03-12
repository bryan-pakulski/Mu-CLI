import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from server.app.main import create_app


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
        assert after_cancel.json()["state"] in {"cancelled", "completed"}

        if after_cancel.json()["state"] == "cancelled":
            resume_response = await client.post(f"/jobs/{job['id']}/resume")
            assert resume_response.status_code == 200
            await asyncio.sleep(0.25)
            after_resume = await client.get(f"/jobs/{job['id']}")
            assert after_resume.status_code == 200
            assert after_resume.json()["state"] in {"running", "completed"}


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
async def test_workspace_index_and_skill_discovery_endpoints() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_session = await client.post(
            "/sessions",
            json={"workspace_path": "/workspace/tools", "mode": "interactive"},
        )
        assert create_session.status_code == 200
        session = create_session.json()

        build = await client.post(f"/sessions/{session['id']}/workspace/index")
        assert build.status_code == 200
        assert build.json()["indexed_files"] >= 1

        indexed = await client.get(f"/sessions/{session['id']}/workspace/index")
        assert indexed.status_code == 200
        assert len(indexed.json()) >= 1

        refreshed = await client.post(f"/sessions/{session['id']}/workspace/index/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["indexed_files"] >= 1
        assert refreshed.json()["next_refresh_after_s"] >= 1

        skills = await client.get("/skills", params={"session_id": session["id"]})
        assert skills.status_code == 200
        assert isinstance(skills.json(), list)
