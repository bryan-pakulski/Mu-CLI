import unittest

from mu_cli.webapp.job_state import JobStatus, JobTerminalReason, set_terminal_reason, transition_job_status


class JobStateTests(unittest.TestCase):
    def test_valid_transition_records_history(self) -> None:
        job = {"status": JobStatus.QUEUED.value}
        result = transition_job_status(job, JobStatus.PLANNING.value, reason="runner_started")
        self.assertTrue(result.ok)
        self.assertEqual(JobStatus.PLANNING.value, job["status"])
        transitions = job.get("status_transitions", [])
        self.assertEqual(1, len(transitions))
        self.assertEqual("queued", transitions[0]["from"])
        self.assertEqual("planning", transitions[0]["to"])

    def test_invalid_transition_rejected(self) -> None:
        job = {"status": JobStatus.QUEUED.value}
        result = transition_job_status(job, JobStatus.COMPLETED.value, reason="bad")
        self.assertFalse(result.ok)
        self.assertEqual(JobStatus.QUEUED.value, job["status"])
        self.assertNotIn("status_transitions", job)

    def test_terminal_transition_rejected(self) -> None:
        job = {"status": JobStatus.COMPLETED.value}
        result = transition_job_status(job, JobStatus.RUNNING.value, reason="reopen")
        self.assertFalse(result.ok)
        self.assertEqual(JobStatus.COMPLETED.value, job["status"])


    def test_set_terminal_reason_accepts_enum(self) -> None:
        job: dict[str, str] = {}
        set_terminal_reason(job, JobTerminalReason.BUDGET_EXHAUSTED)
        self.assertEqual("budget_exhausted", job.get("terminal_reason"))

    def test_set_terminal_reason_rejects_invalid_value(self) -> None:
        with self.assertRaises(ValueError):
            set_terminal_reason({}, "not_a_reason")


    def test_running_must_transition_via_verifying_for_terminal_states(self) -> None:
        job = {"status": JobStatus.RUNNING.value}
        result = transition_job_status(job, JobStatus.TIMED_OUT.value, reason="direct_timeout")
        self.assertFalse(result.ok)
        self.assertEqual(JobStatus.RUNNING.value, job["status"])


if __name__ == "__main__":
    unittest.main()
