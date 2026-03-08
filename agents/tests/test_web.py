import importlib.util
import json
import unittest


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


if __name__ == '__main__':
    unittest.main()
