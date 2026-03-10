import unittest

from mu_cli.webapp.contracts import (
    ContractValidationError,
    parse_chat_request,
    parse_job_kill_request,
    parse_job_plan_request,
    parse_pricing_request,
    parse_session_action_request,
    parse_settings_update_request,
    parse_upload_delete_name,
    parse_uploads_request,
)


class WebContractTests(unittest.TestCase):
    def test_chat_request_contract_table(self) -> None:
        cases = [
            ({"text": "hello"}, True, None),
            ({"text": "  hi  ", "session": "  s1 "}, True, None),
            (["bad"], False, "payload must be a JSON object"),
            ({"text": ""}, False, "text is required"),
            ({"text": "ok", "session": 1}, False, "session must be a string"),
        ]
        for payload, ok, err in cases:
            with self.subTest(payload=payload):
                if ok:
                    req = parse_chat_request(payload, route="/api/chat")
                    self.assertTrue(req.text)
                else:
                    with self.assertRaisesRegex(ContractValidationError, err or ""):
                        parse_chat_request(payload, route="/api/chat")

    def test_session_action_contract_table(self) -> None:
        cases = [
            ({"action": "new", "name": "s1"}, True, None),
            ({"action": "status"}, True, None),
            ({}, False, "action is required"),
            ({"action": "new", "enabled_skills": "x"}, False, "enabled_skills must be a list of strings"),
            ({"action": "new", "agentic_planning": "yes"}, False, "agentic_planning must be a boolean"),
        ]
        for payload, ok, err in cases:
            with self.subTest(payload=payload):
                if ok:
                    req = parse_session_action_request(payload)
                    self.assertTrue(req.action)
                else:
                    with self.assertRaisesRegex(ContractValidationError, err or ""):
                        parse_session_action_request(payload)

    def test_settings_contract_table(self) -> None:
        cases = [
            ({"debug": True}, True, None),
            ({"tool_visibility": {"read_file": False}}, True, None),
            ({"debug": "yes"}, False, "debug must be a boolean"),
            ({"tool_visibility": []}, False, "tool_visibility must be an object"),
            ({"tool_visibility": {"read_file": "no"}}, False, "tool_visibility must be an object of booleans"),
        ]
        for payload, ok, err in cases:
            with self.subTest(payload=payload):
                if ok:
                    req = parse_settings_update_request(payload)
                    self.assertIsInstance(req.payload, dict)
                else:
                    with self.assertRaisesRegex(ContractValidationError, err or ""):
                        parse_settings_update_request(payload)

    def test_job_pricing_upload_contract_table(self) -> None:
        with self.subTest("job kill default reason"):
            req = parse_job_kill_request({})
            self.assertEqual("user requested stop", req.reason)

        with self.subTest("job plan requires decision"):
            with self.assertRaisesRegex(ContractValidationError, "decision is required"):
                parse_job_plan_request({})

        with self.subTest("pricing supports full document"):
            req = parse_pricing_request({"pricing": {"echo": {}}})
            self.assertIn("pricing", req.payload)

        with self.subTest("pricing rejects non-numeric fields"):
            with self.assertRaisesRegex(ContractValidationError, "input_per_1m must be a number"):
                parse_pricing_request({"provider": "echo", "model": "echo", "input_per_1m": "cheap", "output_per_1m": 1.0})

        with self.subTest("uploads request and delete name validation"):
            with self.assertRaisesRegex(ContractValidationError, "no files uploaded"):
                parse_uploads_request([])
            with self.assertRaisesRegex(ContractValidationError, "must not contain path separators"):
                parse_upload_delete_name("../x.txt")


if __name__ == "__main__":
    unittest.main()
