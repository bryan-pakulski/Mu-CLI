import importlib.util
import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("flask") is not None, "flask not installed in this environment")
class WebTests(unittest.TestCase):
    def test_htmx_ui_routes(self) -> None:
        from mu_cli.web import create_app

        app = create_app()
        app.testing = True
        client = app.test_client()

        home = client.get('/')
        self.assertEqual(200, home.status_code)
        self.assertIn('Reactive', home.get_data(as_text=True))
        self.assertNotIn('Advanced settings', home.get_data(as_text=True))

        legacy = client.get('/legacy')
        self.assertEqual(200, legacy.status_code)

        messages = client.get('/ui/messages')
        self.assertEqual(200, messages.status_code)

        state_panel = client.get('/ui/state')
        self.assertEqual(200, state_panel.status_code)
        self.assertIn('session=', state_panel.get_data(as_text=True))

        session_panel = client.get('/ui/session')
        self.assertEqual(200, session_panel.status_code)
        self.assertIn('Session', session_panel.get_data(as_text=True))

        workspace_panel = client.get('/ui/workspace')
        self.assertEqual(200, workspace_panel.status_code)
        self.assertIn('Workspace', workspace_panel.get_data(as_text=True))

        workspace_attach = client.post('/ui/workspace', data={'action': 'attach', 'workspace': '.', 'browse': '.'})
        self.assertEqual(200, workspace_attach.status_code)

        jobs_panel = client.get('/ui/jobs')
        self.assertEqual(200, jobs_panel.status_code)

        settings = client.get('/ui/settings')
        self.assertEqual(200, settings.status_code)
        self.assertIn('Save settings', settings.get_data(as_text=True))
        self.assertIn('Runtime', settings.get_data(as_text=True))
        self.assertIn('Behavior', settings.get_data(as_text=True))
        self.assertIn('Advanced', settings.get_data(as_text=True))

        settings_full = client.get('/ui/settings?variant=full')
        self.assertEqual(200, settings_full.status_code)
        self.assertIn('Behavior', settings_full.get_data(as_text=True))

        settings_save = client.post('/ui/settings', data={
            'provider': 'echo',
            'model': 'echo',
            'approval_mode': 'auto',
            'debug': 'on',
            'agentic_planning': 'on',
            'research_mode': 'on',
            'max_runtime_seconds': '600',
            'condense_enabled': 'on',
            'condense_window': '10',
            'variant': 'quick',
        })
        self.assertEqual(200, settings_save.status_code)
        self.assertIn('Saved.', settings_save.get_data(as_text=True))

        posted = client.post('/ui/chat', data={'text': 'hello from form'})
        self.assertEqual(200, posted.status_code)
        self.assertIn('hello from form', posted.get_data(as_text=True))

        bg = client.post('/ui/chat/background', data={'text': 'hello in background'})
        self.assertEqual(200, bg.status_code)
        self.assertIn('background', bg.get_data(as_text=True).lower())

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
        if not body.get('unchanged'):
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


if __name__ == '__main__':
    unittest.main()
