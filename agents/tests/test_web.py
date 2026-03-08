import importlib.util
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


if __name__ == '__main__':
    unittest.main()
