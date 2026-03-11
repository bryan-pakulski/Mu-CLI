import importlib.util
import io
import os
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("flask") is not None, "flask not installed in this environment")
class WebTests(unittest.TestCase):
    def test_web_state_and_session_endpoints(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.get('/api/state')
        self.assertEqual(200, res.status_code)
        data = res.get_json()
        assert data is not None
        self.assertIn('provider', data)

        res2 = client.post('/api/session', json={'action': 'new', 'name': 'webtest'})
        self.assertEqual(200, res2.status_code)

        res3 = client.post('/api/session', json={'action': 'status'})
        self.assertEqual(200, res3.status_code)
        self.assertEqual('webtest', res3.get_json()['session'])

    def test_chat_endpoint(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/chat', json={'text': 'hello'})
        self.assertEqual(200, res.status_code)
        payload = res.get_json()
        assert payload is not None
        self.assertIn('reply', payload)
        self.assertIn('report', payload)

        state = client.get('/api/state').get_json()
        assert state is not None
        self.assertGreaterEqual(state['session_usage']['total_tokens'], payload['report']['total_tokens'])


    def test_chat_endpoint_rejects_non_object_payload(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/chat', json=['not-an-object'])
        self.assertEqual(400, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertIn('payload must be a JSON object', body.get('error', ''))

    def test_chat_stream_endpoint_rejects_non_string_session(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/chat/stream', json={'text': 'hello', 'session': 123})
        self.assertEqual(400, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertIn('session must be a string', body.get('error', ''))

    def test_session_endpoint_rejects_invalid_enabled_skills_type(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/session', json={'action': 'new', 'name': 'x', 'enabled_skills': 'oops'})
        self.assertEqual(400, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertIn('enabled_skills must be a list of strings', body.get('error', ''))

    def test_settings_endpoint_rejects_invalid_debug_type(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/settings', json={'debug': 'yes'})
        self.assertEqual(400, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertIn('debug must be a boolean', body.get('error', ''))

    def test_session_clear_action_resets_context(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/chat', json={'text': 'hello'})
        self.assertEqual(200, res.status_code)

        cleared = client.post('/api/session', json={'action': 'clear'})
        self.assertEqual(200, cleared.status_code)
        body = cleared.get_json()
        assert body is not None
        self.assertTrue(body['ok'])

        state = client.get('/api/state').get_json()
        assert state is not None
        self.assertEqual([], state['messages'])
        self.assertEqual(0.0, float(state['session_usage']['total_tokens']))

    def test_chat_stream_endpoint(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/chat/stream', json={'text': 'hello'})
        self.assertEqual(200, res.status_code)

        raw = b''.join(res.response).decode('utf-8')
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        self.assertTrue(any(event.get('type') == 'assistant_chunk' for event in events))
        self.assertTrue(any(event.get('type') == 'report' for event in events))
        self.assertEqual('done', events[-1].get('type'))

    def test_chat_stream_endpoint_honors_requested_session(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        create = client.post('/api/session', json={'action': 'new', 'name': 'stream-session'})
        self.assertEqual(200, create.status_code)
        switched = client.post('/api/session', json={'action': 'switch', 'name': 'default'})
        self.assertEqual(200, switched.status_code)

        res = client.post('/api/chat/stream', json={'text': 'hello', 'session': 'stream-session'})
        self.assertEqual(200, res.status_code)
        _ = b''.join(res.response).decode('utf-8')

        status = client.post('/api/session', json={'action': 'status'})
        self.assertEqual(200, status.status_code)
        self.assertEqual('stream-session', status.get_json()['session'])

    def test_web_approval_deny_mode_rejects_mutating_tool(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        client.post('/api/settings', json={'approval_mode': 'deny'})
        res = client.post('/api/chat', json={'text': '/tool write_file {"path":"tmp.txt","content":"x"}'})
        self.assertEqual(200, res.status_code)
        payload = res.get_json()
        assert payload is not None
        self.assertIn('reply', payload)
        self.assertIn('rejected by approval policy', payload['reply']['content'])

    def test_background_job_auto_mode_skips_plan_wait(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        set_mode = client.post('/api/settings', json={'approval_mode': 'auto'})
        self.assertEqual(200, set_mode.status_code)

        start = client.post('/api/chat/background', json={'text': 'Collect two references about autonomous driving in 2025.'})
        self.assertEqual(200, start.status_code)
        payload = start.get_json() or {}
        job_id = payload.get('job_id')
        self.assertTrue(job_id)

        for _ in range(30):
            job_res = client.get(f'/api/jobs/{job_id}')
            self.assertEqual(200, job_res.status_code)
            job = job_res.get_json() or {}
            status = str(job.get('status') or '')
            self.assertNotEqual('awaiting_plan_approval', status)
            if status in {'completed', 'failed', 'timed_out', 'killed'}:
                break
            time.sleep(0.1)


    def test_pricing_endpoint_replaces_full_document(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        payload = {
            'pricing': {
                'openai': {
                    'gpt-test': {'input_per_1m': 1.0, 'output_per_1m': 2.0},
                },
            },
        }
        res = client.post('/api/pricing', json=payload)
        self.assertEqual(200, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertEqual(payload['pricing'], body['pricing'])

    def test_pricing_endpoint_updates_model_row(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/pricing', json={
            'provider': 'echo',
            'model': 'echo',
            'input_per_1m': 1.23,
            'output_per_1m': 4.56,
        })
        self.assertEqual(200, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertEqual(1.23, body['pricing']['echo']['echo']['input_per_1m'])
        self.assertEqual(4.56, body['pricing']['echo']['echo']['output_per_1m'])

    def test_approval_endpoint_rejects_without_pending(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/approval', json={'id': 'missing', 'decision': 'approve'})
        self.assertEqual(404, res.status_code)

    def test_fs_dirs_endpoint(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.get('/api/fs/dirs')
        self.assertEqual(200, res.status_code)
        payload = res.get_json()
        assert payload is not None
        self.assertIn('cwd', payload)
        self.assertIn('children', payload)

    def test_upload_delete_single_file(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post(
            '/api/uploads',
            data={
                'files': [
                    (io.BytesIO(b'alpha'), 'a.txt'),
                    (io.BytesIO(b'beta'), 'b.txt'),
                ]
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(200, res.status_code)

        rm = client.delete('/api/uploads/a.txt')
        self.assertEqual(200, rm.status_code)

        state = client.get('/api/state').get_json()
        assert state is not None
        names = [item['name'] for item in state['uploads']]
        self.assertNotIn('a.txt', names)
        self.assertIn('b.txt', names)


    def test_job_plan_endpoint_rejects_invalid_decision_type(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        bg = client.post('/api/chat/background', json={'text': 'hello'})
        self.assertEqual(200, bg.status_code)
        job_id = bg.get_json()['job_id']

        res = client.post(f'/api/jobs/{job_id}/plan', json={'decision': 7})
        self.assertEqual(400, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertIn('decision must be a string', body.get('error', ''))

    def test_job_kill_endpoint_rejects_invalid_reason_type(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        bg = client.post('/api/chat/background', json={'text': 'hello'})
        self.assertEqual(200, bg.status_code)
        job_id = bg.get_json()['job_id']

        res = client.post(f'/api/jobs/{job_id}/kill', json={'reason': 9})
        self.assertEqual(400, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertIn('reason must be a string', body.get('error', ''))

    def test_pricing_endpoint_rejects_non_numeric_cost_fields(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/pricing', json={
            'provider': 'echo',
            'model': 'echo',
            'input_per_1m': 'cheap',
            'output_per_1m': 1.0,
        })
        self.assertEqual(400, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertIn('input_per_1m must be a number', body.get('error', ''))

    def test_upload_endpoint_rejects_missing_files_payload(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/uploads', data={}, content_type='multipart/form-data')
        self.assertEqual(400, res.status_code)
        body = res.get_json()
        assert body is not None
        self.assertIn('no files uploaded', body.get('error', ''))

    def test_settings_tool_visibility_and_custom_tools(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/settings', json={
            'tool_visibility': {'read_file': False},
            'research_mode': True,
            'custom_tools': [
                {
                    'name': 'say_hi',
                    'description': 'Say hi',
                    'command': ['python', '-c', "print('hi')"],
                    'mutating': False,
                }
            ],
        })
        self.assertEqual(200, res.status_code)

        state = client.get('/api/state').get_json()
        assert state is not None
        tools = {item['name']: item for item in state['tools']}
        self.assertIn('read_file', tools)
        self.assertFalse(tools['read_file']['enabled'])
        self.assertIn('say_hi', tools)
        self.assertEqual('custom', tools['say_hi']['source'])
        self.assertTrue(state['research_mode'])


    def test_state_includes_makefile_agent_tool(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        state = client.get('/api/state').get_json()
        assert state is not None
        tool_names = {item['name'] for item in state.get('tools', [])}
        self.assertIn('run_make_agent_job', tool_names)

    def test_settings_provider_api_keys_override(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/settings', json={
            'openai_api_key': 'sk-test-1',
            'google_api_key': 'g-test-1',
            'ollama_endpoint': 'http://localhost:11434',
        })
        self.assertEqual(200, res.status_code)

        state = client.get('/api/state').get_json()
        assert state is not None
        self.assertEqual('sk-test-1', state['openai_api_key'])
        self.assertEqual('g-test-1', state['google_api_key'])
        self.assertEqual('http://localhost:11434', state['ollama_endpoint'])

    def test_settings_enable_skills(self) -> None:
        from mu_cli.web import create_app

        skills_dir = Path("skills")
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skills_dir / "web-ui-skill.md"
        skill_path.write_text("Always propose polished UI details.", encoding="utf-8")
        self.addCleanup(lambda: skill_path.unlink(missing_ok=True))

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.post('/api/settings', json={'enabled_skills': ['web-ui-skill']})
        self.assertEqual(200, res.status_code)

        state = client.get('/api/state').get_json()
        assert state is not None
        self.assertIn('web-ui-skill', state['skills'])
        self.assertIn('web-ui-skill', state['enabled_skills'])

    def test_skill_content_endpoint(self) -> None:
        from mu_cli.web import create_app

        skills_dir = Path("skills")
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skills_dir / "viewer-skill.md"
        skill_path.write_text("# Viewer\n\ncontent", encoding="utf-8")
        self.addCleanup(lambda: skill_path.unlink(missing_ok=True))

        app = create_app()
        app.testing = True
        client = app.test_client()

        res = client.get('/api/skills/viewer-skill')
        self.assertEqual(200, res.status_code)
        payload = res.get_json()
        assert payload is not None
        self.assertEqual('viewer-skill', payload['name'])
        self.assertIn('Viewer', payload['content'])

    def test_git_repo_and_branch_endpoints(self) -> None:
        from mu_cli.web import create_app

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-b', 'main'], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run(['git', 'config', 'user.email', 'web@example.com'], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run(['git', 'config', 'user.name', 'Web Test'], cwd=repo, check=True, capture_output=True, text=True)
            (repo / 'a.txt').write_text('base\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'a.txt'], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run(['git', 'commit', '-m', 'init'], cwd=repo, check=True, capture_output=True, text=True)

            app = create_app()
            app.testing = True
            client = app.test_client()

            settings = client.post('/api/settings', json={'workspace': str(repo)})
            self.assertEqual(200, settings.status_code)

            repos = client.get(f"/api/git/repos?workspace={repo}")
            self.assertEqual(200, repos.status_code)
            repos_payload = repos.get_json()
            assert repos_payload is not None
            self.assertIn(str(repo), repos_payload['repos'])

            create_branch = client.post('/api/git/branch', json={'action': 'create', 'repo': str(repo), 'branch': 'feature/web'})
            self.assertEqual(200, create_branch.status_code)

            switch_main = client.post('/api/git/branch', json={'action': 'switch', 'repo': str(repo), 'branch': 'main'})
            self.assertEqual(200, switch_main.status_code)

            branches = client.get(f"/api/git/branches?repo={repo}")
            self.assertEqual(200, branches.status_code)
            branches_payload = branches.get_json()
            assert branches_payload is not None
            self.assertIn('feature/web', branches_payload['branches'])
            self.assertEqual('main', branches_payload['current_branch'])

            diff = client.get(f"/api/git/diff?repo={repo}")
            self.assertEqual(200, diff.status_code)
            diff_payload = diff.get_json()
            assert diff_payload is not None
            self.assertIn('status', diff_payload)
            self.assertIn('diff', diff_payload)

    def test_session_turns_are_scoped_to_active_session(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        client.post('/api/session', json={'action': 'new', 'name': 's1'})
        client.post('/api/chat', json={'text': 'one'})
        state1 = client.get('/api/state').get_json()
        assert state1 is not None
        self.assertTrue(all((turn.get('session') == 's1') for turn in state1['session_turns']))

        client.post('/api/session', json={'action': 'new', 'name': 's2'})
        client.post('/api/chat', json={'text': 'two'})
        state2 = client.get('/api/state').get_json()
        assert state2 is not None
        self.assertTrue(all((turn.get('session') == 's2') for turn in state2['session_turns']))

    def test_research_export_endpoint(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        client.post('/api/chat', json={'text': 'hello'})
        res = client.get('/api/research/export?format=json')
        self.assertEqual(200, res.status_code)
        payload = res.get_json()
        assert payload is not None
        self.assertIn('content', payload)
        self.assertIn('format', payload)

    def test_session_condense_action(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()
        client.post('/api/session', json={'action': 'new', 'name': f'condense-{int(time.time() * 1000)}'})

        for idx in range(4):
            res = client.post('/api/chat', json={'text': f'hello {idx}'})
            self.assertEqual(200, res.status_code)

        before_state = client.get('/api/state').get_json()
        assert before_state is not None
        before_count = len(before_state['messages'])

        condense = client.post('/api/session', json={'action': 'condense'})
        self.assertEqual(200, condense.status_code)
        body = condense.get_json()
        assert body is not None
        self.assertTrue(body['ok'])

        after_state = client.get('/api/state').get_json()
        assert after_state is not None
        after_count = len(after_state['messages'])
        self.assertLessEqual(after_count, before_count)
        self.assertTrue(any((m.get('metadata') or {}).get('kind') == 'session_condensed_summary' for m in after_state['messages']))


    def test_job_plan_endpoint_accepts_revised_plan(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        # Seed a synthetic waiting job record
        job_id = 'job-plan-edit'
        state = client.get('/api/state').get_json()
        assert state is not None
        # touch runtime through background endpoint for consistent app state
        client.post('/api/chat/background', json={'text': 'hello plan edit', 'session': state['session']})
        jobs = client.get('/api/jobs').get_json()['jobs']
        assert jobs
        target = jobs[-1]['id']

        revised = 'PLAN:\n1) Validate constraints\n2) Run checks\n3) Summarize.'
        res = client.post(f'/api/jobs/{target}/plan', json={'decision': 'approve', 'revised_plan': revised})
        self.assertEqual(200, res.status_code)
        payload = res.get_json()
        assert payload is not None
        self.assertEqual('approve', payload['decision'])

        job = client.get(f'/api/jobs/{target}').get_json()
        assert job is not None
        self.assertEqual(revised, job['plan'])
        self.assertTrue(any(evt == 'plan: revised_by_user' for evt in job.get('events', [])))

    def test_background_job_can_be_killed(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        client.post('/api/settings', json={'agentic_planning': True, 'max_runtime_seconds': 60})
        started = client.post('/api/chat/background', json={'text': 'long running task for kill switch'})
        self.assertEqual(200, started.status_code)
        job_id = started.get_json()['job_id']

        kill = client.post(f'/api/jobs/{job_id}/kill', json={'reason': 'test kill'})
        self.assertEqual(200, kill.status_code)

        deadline = time.time() + 5
        job = None
        while time.time() < deadline:
            poll = client.get(f'/api/jobs/{job_id}')
            self.assertEqual(200, poll.status_code)
            job = poll.get_json()
            if job['status'] == 'killed':
                break
            time.sleep(0.05)

        assert job is not None
        self.assertEqual('killed', job['status'])
        self.assertTrue(job.get('cancel_requested'))
        self.assertTrue(any('killed' in event for event in job.get('events', [])))

    def test_background_job_tracks_terminal_event(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        client.post('/api/settings', json={'agentic_planning': False, 'max_runtime_seconds': 45})
        res = client.post('/api/chat/background', json={'text': 'hello background'})
        self.assertEqual(200, res.status_code)
        job_id = res.get_json()['job_id']

        deadline = time.time() + 5
        job = None
        while time.time() < deadline:
            poll = client.get(f'/api/jobs/{job_id}')
            self.assertEqual(200, poll.status_code)
            job = poll.get_json()
            if job['status'] in {'completed', 'failed', 'timed_out'}:
                break
            time.sleep(0.05)

        assert job is not None
        self.assertIn(job['status'], {'completed', 'failed', 'timed_out'})
        self.assertTrue(any(event.startswith('status: ') for event in job.get('events', [])))
        self.assertIn('verification_policy', job)
        self.assertIn('checkpoints', job)
        self.assertIsInstance(job.get('checkpoints', []), list)
        self.assertIn('answer_contract', job)


    def test_user_journey_new_chat_stream_clear_happy_path(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        created = client.post('/api/session', json={'action': 'new', 'name': 'journey-a'})
        self.assertEqual(200, created.status_code)

        chat = client.post('/api/chat', json={'text': 'hello from journey'})
        self.assertEqual(200, chat.status_code)
        chat_body = chat.get_json()
        assert chat_body is not None
        self.assertIn('reply', chat_body)

        stream = client.post('/api/chat/stream', json={'text': 'follow up from stream'})
        self.assertEqual(200, stream.status_code)
        raw = b''.join(stream.response).decode('utf-8')
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        self.assertTrue(any(event.get('type') == 'assistant_chunk' for event in events))
        self.assertEqual('done', events[-1].get('type'))

        cleared = client.post('/api/session', json={'action': 'clear'})
        self.assertEqual(200, cleared.status_code)

        state = client.get('/api/state').get_json()
        assert state is not None
        self.assertEqual('journey-a', state['session'])
        self.assertEqual([], state['messages'])
        self.assertEqual(0.0, float(state['session_usage']['total_tokens']))

    def test_user_journey_with_upload_and_clear_happy_path(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        created = client.post('/api/session', json={'action': 'new', 'name': 'journey-b'})
        self.assertEqual(200, created.status_code)

        up = client.post(
            '/api/uploads',
            data={'files': [(io.BytesIO(b'note for context'), 'note.txt')]},
            content_type='multipart/form-data',
        )
        self.assertEqual(200, up.status_code)

        chat = client.post('/api/chat', json={'text': 'use uploaded note'})
        self.assertEqual(200, chat.status_code)

        stream = client.post('/api/chat/stream', json={'text': 'stream after upload'})
        self.assertEqual(200, stream.status_code)
        _ = b''.join(stream.response).decode('utf-8')

        clear_uploads = client.delete('/api/uploads')
        self.assertEqual(200, clear_uploads.status_code)
        self.assertGreaterEqual(clear_uploads.get_json()['removed'], 1)

        cleared = client.post('/api/session', json={'action': 'clear'})
        self.assertEqual(200, cleared.status_code)

    def test_state_includes_telemetry_snapshot(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        state = client.get('/api/state').get_json()
        assert state is not None
        telemetry = state.get('telemetry') or {}
        self.assertIn('total_requests', telemetry)
        self.assertIn('action_counts', telemetry)

    def test_telemetry_action_counts_increment_for_chat_and_session(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        client.post('/api/chat', json={'text': 'telemetry chat'})
        client.post('/api/session', json={'action': 'clear'})

        telemetry_res = client.get('/api/telemetry')
        self.assertEqual(200, telemetry_res.status_code)
        telemetry = (telemetry_res.get_json() or {}).get('telemetry') or {}
        actions = telemetry.get('action_counts') or {}
        self.assertGreaterEqual(int(actions.get('chat_turn', 0)), 1)
        self.assertGreaterEqual(int(actions.get('session_clear', 0)), 1)


    def test_traces_persist_across_app_restart(self) -> None:
        from mu_cli.web import create_app

        with tempfile.TemporaryDirectory() as td:
            prev = Path.cwd()
            os.chdir(td)
            try:
                app1 = create_app()
                app1.testing = True
                client1 = app1.test_client()

                res = client1.post('/api/chat', json={'text': 'run a quick check'})
                self.assertEqual(200, res.status_code)
                state1 = client1.get('/api/state').get_json()
                assert state1 is not None
                self.assertGreater(len(state1['traces']), 0)

                app2 = create_app()
                app2.testing = True
                client2 = app2.test_client()
                state2 = client2.get('/api/state').get_json()
                assert state2 is not None
                self.assertGreater(len(state2['traces']), 0)
            finally:
                os.chdir(prev)

    def test_clear_all_stored_data_endpoint(self) -> None:
        from mu_cli.web import create_app

        with tempfile.TemporaryDirectory() as td:
            prev = Path.cwd()
            os.chdir(td)
            try:
                app = create_app()
                app.testing = True
                client = app.test_client()

                client.post('/api/chat', json={'text': 'hello clear-all'})
                client.post(
                    '/api/uploads',
                    data={'files': [(io.BytesIO(b'persisted note'), 'note.txt')]},
                    content_type='multipart/form-data',
                )

                before = client.get('/api/state').get_json()
                assert before is not None
                self.assertGreater(len(before['traces']), 0)
                self.assertGreaterEqual(len(before['uploads']), 1)

                cleared = client.post('/api/state/clear-all', json={})
                self.assertEqual(200, cleared.status_code)
                cleared_body = cleared.get_json()
                assert cleared_body is not None
                self.assertTrue(cleared_body.get('ok'))

                after = client.get('/api/state').get_json()
                assert after is not None
                self.assertEqual('default', after['session'])
                self.assertEqual([], after['traces'])
                self.assertEqual([], after['uploads'])
                self.assertEqual([], after['messages'])
            finally:
                os.chdir(prev)



if __name__ == '__main__':
    unittest.main()
